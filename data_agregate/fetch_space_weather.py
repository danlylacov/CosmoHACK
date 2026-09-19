#!/usr/bin/env python3
"""
Выгрузка SEISS (MPS-HI + SGPS) и геомагнитных индексов за lookback от текущего UTC.

Каденс выхода — 30 минут: Hp30/ap30 с GFZ (нативные 30 min), SEISS усредняется
со всех 5-min каналов SWPC (официального 30-min SEISS нет).

Источники:
  - SWPC JSON (оперативные 5-min, от текущей даты): https://services.swpc.noaa.gov/json/goes/
  - NCEI GOES-R SEISS L2 5-min (архив): https://www.ncei.noaa.gov/products/goes-r-space-environment-in-situ
  - GFZ JSON Hp30/ap30/Kp: https://kp.gfz.de/en/data
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cftime
import netCDF4 as nc
import numpy as np
import requests

NCEI_BASE = (
    "https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes"
)
KP_URL = "https://kp.gfz.de/app/json/"
SWPC_BASE = "https://services.swpc.noaa.gov/json/goes"
PRODUCTS = ("mpsh", "sgps")
SAT_BY_YEAR = {2023: 18, 2024: 18, 2025: 19, 2026: 19}

# SWPC 5-min feeds derived from SEISS: electrons = MPS-HI, protons/alphas = SGPS.
SWPC_FEEDS = {
    "mpsh": ("differential-electrons", "integral-electrons"),
    "sgps": ("differential-protons", "integral-protons", "differential-alphas"),
}
GFZ_30MIN_INDICES = ("Hp30", "ap30")
GFZ_3H_INDICES = ("Kp",)
OUTPUT_INTERVAL_MINUTES = 30

FILENAME_RE = re.compile(
    r"sci_(mpsh|sgps)-l2-avg5m_g(\d{2})_d(\d{8})_v([0-9-]+)\.nc",
    re.IGNORECASE,
)
HREF_RE = re.compile(
    r'href=["\']?(sci_(?:mpsh|sgps)-l2-avg5m_g\d{2}_d\d{8}_v[0-9-]+\.nc)["\']?',
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": "CosmoHACK-SEISS-Kp-Fetcher/1.0 (space weather research)"
}
TIMEOUT = 120


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_z(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def satellite_for_date(day) -> int:
    year = day.year if hasattr(day, "year") else int(day)
    sat = SAT_BY_YEAR.get(year)
    if sat is None:
        raise ValueError(
            f"Нет назначенного спутника для года {year}. "
            "Поддерживаются 2023–2024 (GOES-18) и 2025–2026 (GOES-19)."
        )
    return sat


def dates_in_window(start: datetime, end: datetime):
    day = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    while day <= last:
        yield day
        day += timedelta(days=1)


def version_key(version: str) -> tuple:
    parts = []
    for token in version.split("-"):
        try:
            parts.append(int(token))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def numpy_to_json(obj):
    if isinstance(obj, np.ma.MaskedArray):
        if obj.ndim == 0:
            return None if np.ma.is_masked(obj) else numpy_to_json(obj.item())
        return [numpy_to_json(item) for item in obj]
    if isinstance(obj, np.ndarray):
        if obj.ndim == 0:
            return numpy_to_json(obj.item())
        return [numpy_to_json(item) for item in obj]
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(obj, (np.bytes_, bytes, bytearray)):
        return bytes(obj).decode("utf-8", errors="replace").rstrip("\x00")
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    if isinstance(obj, (list, tuple)):
        return [numpy_to_json(item) for item in obj]
    return obj


def to_aware_utc(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime(
        int(value.year),
        int(value.month),
        int(value.day),
        int(getattr(value, "hour", 0)),
        int(getattr(value, "minute", 0)),
        int(getattr(value, "second", 0)),
        int(getattr(value, "microsecond", 0)),
        tzinfo=timezone.utc,
    )


def list_month_files(sat: int, product: str, year: int, month: int) -> list[str]:
    url = (
        f"{NCEI_BASE}/goes{sat}/l2/data/{product}-l2-avg5m/"
        f"{year:04d}/{month:02d}/"
    )
    print(f"  listing: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    names = HREF_RE.findall(resp.text)
    # findall with one group returns the group; keep unique order
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def pick_file_for_day(filenames: list[str], product: str, sat: int, yyyymmdd: str) -> str | None:
    matches = []
    for name in filenames:
        m = FILENAME_RE.fullmatch(name)
        if not m:
            continue
        if m.group(1).lower() != product:
            continue
        if int(m.group(2)) != sat:
            continue
        if m.group(3) != yyyymmdd:
            continue
        matches.append((version_key(m.group(4)), name))
    if not matches:
        return None
    matches.sort()
    return matches[-1][1]


def download_nc(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading: {url}")
    with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fh.write(chunk)
    tmp.replace(dest)


def floor_interval(dt: datetime, minutes: int = OUTPUT_INTERVAL_MINUTES) -> datetime:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    step = minutes * 60
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % step), tz=timezone.utc)


def fetch_gfz_index(start: datetime, end: datetime, index: str) -> dict:
    params = {
        "start": iso_z(start),
        "end": iso_z(end),
        "index": index,
    }
    print(f"{index}: {KP_URL} start={params['start']} end={params['end']}")
    resp = requests.get(KP_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    times = payload.get("datetime") or []
    values = payload.get(index) or []
    has_status = index not in ("Hp30", "Hp60", "ap30", "ap60", "Fobs", "Fadj")
    statuses = payload.get("status") if has_status else None
    if statuses is None:
        statuses = [None] * len(times)

    keep_t, keep_v, keep_s = [], [], []
    for ts, value, status in zip(times, values, statuses):
        moment = parse_iso(ts)
        if start <= moment <= end:
            keep_t.append(iso_z(moment))
            keep_v.append(numpy_to_json(value))
            keep_s.append(status)

    interval = 30 if index in ("Hp30", "ap30") else 60 if index in ("Hp60", "ap60") else 180
    result = {
        "datetime": keep_t,
        index: keep_v,
        "meta": payload.get("meta") or payload.get("metadata") or {},
        "source": KP_URL,
        "interval_minutes": interval,
    }
    if has_status:
        result["status"] = keep_s
    return result


def fetch_geomagnetic(start: datetime, end: datetime) -> dict:
    payload = {
        "interval_minutes": OUTPUT_INTERVAL_MINUTES,
        "source": KP_URL,
    }
    for index in GFZ_30MIN_INDICES + GFZ_3H_INDICES:
        payload[index] = fetch_gfz_index(start, end, index)
    return payload


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mean_numbers(values):
    nums = [v for v in values if _is_number(v)]
    if not nums:
        return None
    return float(sum(nums) / len(nums))


def merge_values(items):
    """Усредняет числа, списки и словари; метки (energy/channel) сохраняет."""
    present = [item for item in items if item is not None]
    if not present:
        return None
    first = present[0]
    if isinstance(first, dict):
        keys = []
        for item in present:
            if isinstance(item, dict):
                for key in item:
                    if key not in keys:
                        keys.append(key)
        return {
            key: merge_values(
                [item.get(key) if isinstance(item, dict) else None for item in present]
            )
            for key in keys
        }
    if isinstance(first, list):
        if first and isinstance(first[0], dict) and (
            "energy" in first[0] or "channel" in first[0]
        ):
            groups: dict[tuple, list] = defaultdict(list)
            order = []
            for lst in present:
                if not isinstance(lst, list):
                    continue
                for meas in lst:
                    if not isinstance(meas, dict):
                        continue
                    key = (
                        meas.get("channel"),
                        meas.get("energy"),
                        meas.get("satellite"),
                    )
                    if key not in groups:
                        order.append(key)
                    groups[key].append(meas)
            return [merge_values(groups[key]) for key in order]
        width = max(len(item) for item in present if isinstance(item, list))
        return [
            merge_values(
                [
                    item[idx] if isinstance(item, list) and idx < len(item) else None
                    for item in present
                ]
            )
            for idx in range(width)
        ]
    if any(_is_number(item) for item in present):
        return _mean_numbers(present)
    return first


def resample_records(records: list[dict], minutes: int = OUTPUT_INTERVAL_MINUTES) -> list[dict]:
    buckets: dict[str, list] = defaultdict(list)
    for rec in records:
        raw = rec.get("time")
        if not raw:
            continue
        buckets[iso_z(floor_interval(parse_iso(str(raw)), minutes))].append(rec)
    out = []
    for ts in sorted(buckets):
        merged = merge_values(buckets[ts]) or {}
        merged["time"] = ts
        merged["interval_minutes"] = minutes
        merged["n_samples"] = len(buckets[ts])
        out.append(merged)
    return out


def find_time_variable(ds: nc.Dataset):
    for name in ("L2_SciData_TimeStamp", "time"):
        if name in ds.variables:
            return name, ds.variables[name]
    raise KeyError("В NetCDF нет переменной времени (L2_SciData_TimeStamp / time)")


def decode_times(var) -> list[datetime]:
    raw = var[:]
    units = getattr(var, "units", None)
    if units:
        try:
            decoded = cftime.num2pydate(raw, units)
        except Exception:
            decoded = raw
    else:
        decoded = raw
    if not hasattr(decoded, "__len__") or isinstance(decoded, (str, bytes)):
        decoded = [decoded]
    return [to_aware_utc(item) for item in decoded]


def variable_attrs(var) -> dict:
    attrs = {}
    for key in var.ncattrs():
        attrs[key] = numpy_to_json(var.getncattr(key))
    return attrs


def time_axis_index(var, time_name: str, time_dim: str | None) -> int | None:
    dims = list(var.dimensions)
    if time_name in dims:
        return dims.index(time_name)
    if time_dim and time_dim in dims:
        return dims.index(time_dim)
    return None


def slice_along_axis(values, axis: int, indices: np.ndarray):
    indexer = [slice(None)] * values.ndim
    indexer[axis] = indices
    return values[tuple(indexer)]


def read_seiss_file(path: Path, start: datetime, end: datetime, satellite: str) -> dict:
    ds = nc.Dataset(path, "r")
    try:
        time_name, time_var = find_time_variable(ds)
        times = decode_times(time_var)
        time_dim = time_var.dimensions[0] if time_var.dimensions else None
        mask = np.array([start <= t <= end for t in times], dtype=bool)
        selected = np.nonzero(mask)[0]

        attrs = {name: variable_attrs(var) for name, var in ds.variables.items()}
        static = {}
        timed_names = []
        timed_values = {}

        for name, var in ds.variables.items():
            if name == time_name:
                timed_names.append(name)
                continue
            axis = time_axis_index(var, time_name, time_dim)
            data = var[:]
            if axis is None:
                static[name] = numpy_to_json(data)
            else:
                timed_names.append(name)
                timed_values[name] = slice_along_axis(data, axis, selected)

        records = []
        for local_i, global_i in enumerate(selected.tolist()):
            record = {
                "time": iso_z(times[global_i]),
                "satellite": satellite,
            }
            for name in timed_names:
                if name == time_name:
                    continue
                values = timed_values[name]
                axis = time_axis_index(ds.variables[name], time_name, time_dim)
                if axis == 0:
                    point = values[local_i]
                else:
                    point = np.take(values, local_i, axis=axis)
                record[name] = numpy_to_json(point)
            records.append(record)

        global_attrs = {
            key: numpy_to_json(ds.getncattr(key)) for key in ds.ncattrs()
        }
        return {
            "records": records,
            "static": static,
            "attrs": attrs,
            "global_attrs": global_attrs,
            "time_variable": time_name,
        }
    finally:
        ds.close()


def merge_static(existing: dict, incoming: dict) -> dict:
    if not existing:
        return incoming
    merged = dict(existing)
    for key, value in incoming.items():
        if key not in merged:
            merged[key] = value
    return merged


def fetch_seiss_product(
    product: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    listing_cache: dict,
) -> tuple[dict, list[dict]]:
    files_used = []
    satellites = []
    records = []
    static = {}
    attrs = {}
    global_attrs = {}
    missing = []
    time_variable = None

    for day in dates_in_window(start, end):
        sat = satellite_for_date(day)
        sat_id = f"g{sat}"
        yyyymmdd = day.strftime("%Y%m%d")
        list_key = (sat, product, day.year, day.month)
        if list_key not in listing_cache:
            listing_cache[list_key] = list_month_files(sat, product, day.year, day.month)
        filename = pick_file_for_day(listing_cache[list_key], product, sat, yyyymmdd)
        if filename is None:
            item = {
                "product": product,
                "satellite": sat_id,
                "date": day.isoformat(),
                "reason": "file not listed on NCEI",
            }
            missing.append(item)
            print(f"  missing {product} {sat_id} {day.isoformat()}: нет файла в каталоге")
            continue

        url = (
            f"{NCEI_BASE}/goes{sat}/l2/data/{product}-l2-avg5m/"
            f"{day.year:04d}/{day.month:02d}/{filename}"
        )
        dest = cache_dir / f"goes{sat}" / product / filename
        try:
            if not dest.exists():
                download_nc(url, dest)
            else:
                print(f"  cache hit: {dest.name}")
            parsed = read_seiss_file(dest, start, end, sat_id)
        except Exception as exc:
            item = {
                "product": product,
                "satellite": sat_id,
                "date": day.isoformat(),
                "file": filename,
                "reason": str(exc),
            }
            missing.append(item)
            print(f"  error {product} {sat_id} {day.isoformat()}: {exc}")
            continue

        files_used.append(
            {
                "file": filename,
                "url": url,
                "satellite": sat_id,
                "date": day.isoformat(),
            }
        )
        satellites.append(sat_id)
        records.extend(parsed["records"])
        static = merge_static(static, parsed["static"])
        if not attrs:
            attrs = parsed["attrs"]
        if not global_attrs:
            global_attrs = parsed["global_attrs"]
        time_variable = parsed["time_variable"]

    unique_sats = list(dict.fromkeys(satellites))
    records.sort(key=lambda row: row["time"])
    payload = {
        "satellite": unique_sats[0] if len(unique_sats) == 1 else unique_sats,
        "satellites": unique_sats,
        "files": files_used,
        "records": records,
        "static": static,
        "attrs": attrs,
        "global_attrs": global_attrs,
        "time_variable": time_variable,
    }
    return payload, missing


def swpc_span(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600.0
    if hours <= 5:
        return "6-hour"
    if hours <= 20:
        return "1-day"
    if hours <= 68:
        return "3-day"
    return "7-day"


def load_json_url(url: str, dest: Path | None = None):
    if dest is not None and dest.exists():
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            dest.stat().st_mtime, tz=timezone.utc
        )
        if age <= timedelta(minutes=4):
            print(f"  cache hit: {dest.name}")
            return json.loads(dest.read_text(encoding="utf-8"))
    print(f"  downloading: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if dest is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def fetch_swpc_feeds(start: datetime, end: datetime, cache_dir: Path) -> tuple[dict, list[str], dict]:
    span = swpc_span(end - start)
    cache = cache_dir / "swpc"
    urls = []
    feeds: dict[str, list] = defaultdict(list)
    for role in ("primary", "secondary"):
        for product_feeds in SWPC_FEEDS.values():
            for feed in product_feeds:
                filename = f"{feed}-{span}.json"
                url = f"{SWPC_BASE}/{role}/{filename}"
                urls.append(url)
                dest = cache / role / filename
                try:
                    rows = load_json_url(url, dest)
                except Exception as exc:
                    print(f"  error {url}: {exc}")
                    continue
                if isinstance(rows, list):
                    feeds[feed].extend(rows)
    try:
        sources = load_json_url(
            f"{SWPC_BASE}/instrument-sources.json",
            cache / "instrument-sources.json",
        )
    except Exception:
        sources = []
    return feeds, urls, {"span": span, "instrument_sources": sources}


def swpc_records(feeds: dict, feed_names: tuple[str, ...], start: datetime, end: datetime) -> list[dict]:
    buckets: dict[str, dict] = {}
    for feed in feed_names:
        for row in feeds.get(feed, []):
            raw_time = row.get("time_tag")
            if not raw_time:
                continue
            moment = parse_iso(str(raw_time))
            if not (start <= moment <= end):
                continue
            try:
                want = satellite_for_date(moment)
            except ValueError:
                continue
            sat = int(row.get("satellite", -1))
            if sat != want:
                continue
            key = iso_z(moment)
            rec = buckets.setdefault(
                key,
                {
                    "time": key,
                    "satellite": f"g{sat}",
                    "source": "swpc",
                },
            )
            item = {
                name: numpy_to_json(value)
                for name, value in row.items()
                if name != "time_tag"
            }
            rec.setdefault(feed.replace("-", "_"), []).append(item)
    records = [buckets[ts] for ts in sorted(buckets)]
    return records


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Выгрузка SEISS (все каналы) и Hp30/ap30/Kp за lookback от текущего UTC. Выход SEISS — 30-min средние."
    )
    span = parser.add_mutually_exclusive_group()
    span.add_argument("--days", type=float, help="Lookback в сутках от текущего UTC.")
    span.add_argument("--hours", type=float, help="Lookback в часах от текущего UTC.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Каталог для space_weather.json",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(__file__).resolve().parent / "cache",
        help="Каталог сырых NetCDF",
    )
    return parser.parse_args(argv)


def lookback_delta(args: argparse.Namespace) -> timedelta:
    if args.hours is not None:
        if args.hours <= 0:
            raise SystemExit("--hours должен быть > 0")
        return timedelta(hours=args.hours)
    days = 1.0 if args.days is None else args.days
    if days <= 0:
        raise SystemExit("--days должен быть > 0")
    return timedelta(days=days)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    end = utcnow()
    start = end - lookback_delta(args)
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)

    print(f"Окно: {iso_z(start)} → {iso_z(end)}")

    missing_files: list[dict] = []
    geomagnetic = fetch_geomagnetic(start, end)

    print("SWPC GOES JSON (оперативные 5-min → средние 30 min, все каналы)")
    swpc_feeds, swpc_urls, swpc_meta = fetch_swpc_feeds(start, end, args.cache)

    listing_cache: dict = {}
    seiss = {}
    for product in PRODUCTS:
        print(f"SEISS {product}: SWPC 30-min + NCEI archive")
        realtime_5min = swpc_records(swpc_feeds, SWPC_FEEDS[product], start, end)
        realtime = resample_records(realtime_5min, OUTPUT_INTERVAL_MINUTES)
        ncei_payload, missing = fetch_seiss_product(
            product, start, end, args.cache, listing_cache
        )
        missing_files.extend(missing)
        if ncei_payload.get("records"):
            ncei_payload = dict(ncei_payload)
            ncei_payload["records_5min"] = ncei_payload["records"]
            ncei_payload["records"] = resample_records(
                ncei_payload["records"], OUTPUT_INTERVAL_MINUTES
            )
        sats = list(
            dict.fromkeys(
                rec.get("satellite")
                for rec in realtime
                if rec.get("satellite")
            )
        )
        seiss[product] = {
            "satellite": sats[0] if len(sats) == 1 else sats,
            "satellites": sats,
            "source": SWPC_BASE,
            "span": swpc_meta["span"],
            "interval_minutes": OUTPUT_INTERVAL_MINUTES,
            "files": swpc_urls,
            "records": realtime,
            "records_5min": realtime_5min,
            "static": {},
            "instrument_sources": swpc_meta.get("instrument_sources"),
            "ncei": ncei_payload,
        }

    result = {
        "fetched_at": iso_z(end),
        "window": {"start": iso_z(start), "end": iso_z(end)},
        "interval_minutes": OUTPUT_INTERVAL_MINUTES,
        "kp": geomagnetic,
        "seiss": seiss,
        "missing_files": missing_files,
    }

    out_path = args.out / "space_weather.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(
        f"Готово: {out_path} "
        f"(Hp30={len(geomagnetic['Hp30']['datetime'])}, "
        f"ap30={len(geomagnetic['ap30']['datetime'])}, "
        f"Kp={len(geomagnetic['Kp']['datetime'])}, "
        f"mpsh_30min={len(seiss['mpsh']['records'])}, "
        f"sgps_30min={len(seiss['sgps']['records'])}, "
        f"missing={len(missing_files)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
