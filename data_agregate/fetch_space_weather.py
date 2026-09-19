#!/usr/bin/env python3
"""
Выгрузка SEISS (MPS-HI + SGPS) и геомагнитных индексов за lookback от текущего UTC.

Каденс выхода — 30 минут: Hp30/ap30 с GFZ (нативные 30 min), SEISS усредняется
со всех 5-min каналов SWPC (официального 30-min SEISS нет).

Источники:
  - SWPC JSON SEISS/MAG (оперативные 5-min / 1-min): https://services.swpc.noaa.gov/json/goes/
  - SWPC GOES EXIS: xrays, euvs (primary+secondary)
  - SWPC RTSW: mag+wind 1-min (ACE / DSCOVR SOLAR1 / IMAP)
  - SWPC estimated Kp 1-min и Geospace Dst
  - NCEI L1b daily MPS-HI/SGPS/EHIS: data.ngdc.noaa.gov .../l1b/seis-l1b-*
  - AWS NODD 30-s гранулы (не качаем целиком: тысячи файлов/сутки): s3://noaa-goes19/
  - GFZ JSON индексы: https://kp.gfz.de/en/data
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
SWPC_MAG_FEED = "magnetometers"
SWPC_EXIS_FEEDS = ("xrays", "euvs")
RTSW_BASE = "https://services.swpc.noaa.gov/json/rtsw"
SWPC_KP_URL = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
SWPC_DST_URL = "https://services.swpc.noaa.gov/json/geospace/geospace_dst_7_day.json"
SWPC_F107_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"
GFZ_INDICES = (
    "Hp30",
    "ap30",
    "Hp60",
    "ap60",
    "Kp",
    "ap",
    "Ap",
    "Cp",
    "C9",
    "SN",
    "Fobs",
    "Fadj",
)
OUTPUT_INTERVAL_MINUTES = 30

FILENAME_RE = re.compile(
    r"sci_(mpsh|sgps)-l2-avg5m_g(\d{2})_d(\d{8})_v([0-9-]+)\.nc",
    re.IGNORECASE,
)
HREF_RE = re.compile(
    r'href=["\']?(sci_(?:mpsh|sgps)-l2-avg5m_g\d{2}_d\d{8}_v[0-9-]+\.nc)["\']?',
    re.IGNORECASE,
)
L1B_FILENAME_RE = re.compile(
    r"ops_seis-l1b-(mpsh|sgps|ehis|mpsl)_g(\d{2})_d(\d{8})_v([0-9-]+)\.nc",
    re.IGNORECASE,
)
L1B_HREF_RE = re.compile(
    r'href=["\']?(ops_seis-l1b-(?:mpsh|sgps|ehis|mpsl)_g\d{2}_d\d{8}_v[0-9-]+\.nc)["\']?',
    re.IGNORECASE,
)
L1B_EXTRA = ("ehis",)

HEADERS = {
    "User-Agent": "CosmoHACK-SEISS-Kp-Fetcher/1.0 (space weather research)"
}
TIMEOUT = 300


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

    if index in ("Hp30", "ap30"):
        interval = 30
    elif index in ("Hp60", "ap60"):
        interval = 60
    elif index in ("SN", "Fobs", "Fadj", "Ap", "Cp"):
        interval = 1440
    else:
        interval = 180
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
    for index in GFZ_INDICES:
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
            key: (
                next(
                    (
                        item.get(key)
                        for item in present
                        if isinstance(item, dict) and item.get(key) is not None
                    ),
                    None,
                )
                if key in {"satellite", "source", "channel", "energy", "line", "time"}
                else merge_values(
                    [item.get(key) if isinstance(item, dict) else None for item in present]
                )
            )
            for key in keys
        }
    if isinstance(first, list):
        if first and isinstance(first[0], dict) and (
            "energy" in first[0] or "channel" in first[0] or "line" in first[0]
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
                        meas.get("line"),
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


def list_l1b_month_files(sat: int, product: str, year: int, month: int) -> list[str]:
    url = (
        f"{NCEI_BASE}/goes{sat}/l1b/seis-l1b-{product}/"
        f"{year:04d}/{month:02d}/"
    )
    print(f"  listing L1b: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    names = L1B_HREF_RE.findall(resp.text)
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def pick_l1b_file(filenames: list[str], product: str, sat: int, yyyymmdd: str) -> str | None:
    matches = []
    for name in filenames:
        m = L1B_FILENAME_RE.fullmatch(name)
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


def _decode_char_labels(arr) -> list:
    data = np.array(arr)
    if data.ndim == 0:
        value = data.item()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace").strip("\x00 ")
        return value
    if data.ndim == 1 and data.dtype.kind in ("S", "U", "O"):
        out = []
        for item in data:
            if isinstance(item, bytes):
                out.append(item.decode("utf-8", errors="replace").strip("\x00 "))
            else:
                out.append(str(item).strip("\x00 "))
        return out
    if data.ndim >= 2:
        rows = []
        for row in data:
            chars = []
            for item in np.ravel(row):
                if isinstance(item, bytes):
                    chars.append(item.decode("utf-8", errors="replace"))
                else:
                    chars.append(str(item))
            rows.append("".join(chars).strip("\x00 "))
        return rows
    return numpy_to_json(data)


def _l1b_time_values(ds: nc.Dataset):
    for name in (
        "L1a_SciData_TimeStamp",
        "L2_SciData_TimeStamp",
        "time",
        "product_time",
        "ELF_StartStopTime",
        "PEC_StartStopTime",
        "HCR_StartStop_Time",
    ):
        if name not in ds.variables:
            continue
        var = ds.variables[name]
        raw = var[:]
        if getattr(raw, "ndim", 1) > 1:
            raw = raw[:, 0]
        units = getattr(var, "units", None)
        if units:
            try:
                decoded = cftime.num2pydate(raw, units)
            except Exception:
                decoded = raw
        else:
            decoded = raw
        times = [to_aware_utc(item) for item in decoded]
        return name, times, var.dimensions[0] if var.dimensions else None
    raise KeyError("В L1b NetCDF нет переменной времени")


def read_l1b_binned(path: Path, start: datetime, end: datetime, satellite: str) -> dict:
    ds = nc.Dataset(path, "r")
    try:
        time_name, times, time_dim = _l1b_time_values(ds)
        n_time = len(times)
        mask = np.array([start <= t <= end for t in times], dtype=bool)
        if not np.any(mask):
            return {
                "records": [],
                "static": {},
                "attrs": {name: variable_attrs(var) for name, var in ds.variables.items()},
                "global_attrs": {
                    key: numpy_to_json(ds.getncattr(key)) for key in ds.ncattrs()
                },
                "time_variable": time_name,
            }

        bin_keys = np.array(
            [iso_z(floor_interval(t, OUTPUT_INTERVAL_MINUTES)) if keep else "" for t, keep in zip(times, mask)],
            dtype=object,
        )
        unique_bins = sorted({key for key in bin_keys if key})
        bin_index = {key: i for i, key in enumerate(unique_bins)}
        ids = np.full(n_time, -1, dtype=np.int32)
        for i, key in enumerate(bin_keys):
            if key:
                ids[i] = bin_index[key]

        attrs = {name: variable_attrs(var) for name, var in ds.variables.items()}
        static = {}
        timed_means: dict[str, list] = {}

        for name, var in ds.variables.items():
            if name == time_name:
                continue
            data = var[:]
            if data.dtype.kind in ("S", "U"):
                static[name] = _decode_char_labels(data)
                continue
            if getattr(data, "shape", ())[:1] != (n_time,):
                static[name] = numpy_to_json(data)
                continue
            means = []
            for key in unique_bins:
                sl = data[ids == bin_index[key]]
                sl = np.ma.masked_invalid(sl)
                means.append(numpy_to_json(sl.mean(axis=0)))
            timed_means[name] = means

        records = []
        for i, key in enumerate(unique_bins):
            rec = {
                "time": key,
                "satellite": satellite,
                "source": "ncei-l1b",
                "interval_minutes": OUTPUT_INTERVAL_MINUTES,
                "n_samples": int(np.sum(ids == i)),
            }
            for name, series in timed_means.items():
                rec[name] = series[i]
            records.append(rec)

        return {
            "records": records,
            "static": static,
            "attrs": attrs,
            "global_attrs": {
                key: numpy_to_json(ds.getncattr(key)) for key in ds.ncattrs()
            },
            "time_variable": time_name,
        }
    finally:
        ds.close()


def fetch_l1b_product(
    product: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    listing_cache: dict,
    only_dates: set[str] | None = None,
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
        if only_dates is not None and day.isoformat() not in only_dates:
            continue
        sat = satellite_for_date(day)
        sat_id = f"g{sat}"
        yyyymmdd = day.strftime("%Y%m%d")
        list_key = ("l1b", sat, product, day.year, day.month)
        if list_key not in listing_cache:
            listing_cache[list_key] = list_l1b_month_files(sat, product, day.year, day.month)
        filename = pick_l1b_file(listing_cache[list_key], product, sat, yyyymmdd)
        if filename is None:
            missing.append(
                {
                    "product": f"{product}-l1b",
                    "satellite": sat_id,
                    "date": day.isoformat(),
                    "reason": "L1b file not listed on NCEI",
                }
            )
            print(f"  missing L1b {product} {sat_id} {day.isoformat()}")
            continue

        url = (
            f"{NCEI_BASE}/goes{sat}/l1b/seis-l1b-{product}/"
            f"{day.year:04d}/{day.month:02d}/{filename}"
        )
        dest = cache_dir / f"goes{sat}" / f"{product}-l1b" / filename
        try:
            if not dest.exists():
                download_nc(url, dest)
            else:
                print(f"  cache hit: {dest.name}")
            parsed = read_l1b_binned(dest, start, end, sat_id)
        except Exception as exc:
            missing.append(
                {
                    "product": f"{product}-l1b",
                    "satellite": sat_id,
                    "date": day.isoformat(),
                    "file": filename,
                    "reason": str(exc),
                }
            )
            print(f"  error L1b {product} {sat_id} {day.isoformat()}: {exc}")
            continue

        files_used.append(
            {"file": filename, "url": url, "satellite": sat_id, "date": day.isoformat()}
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
    return {
        "satellite": unique_sats[0] if len(unique_sats) == 1 else unique_sats,
        "satellites": unique_sats,
        "source": f"{NCEI_BASE}/.../l1b/seis-l1b-{product}/",
        "interval_minutes": OUTPUT_INTERVAL_MINUTES,
        "files": files_used,
        "records": records,
        "static": static,
        "attrs": attrs,
        "global_attrs": global_attrs,
        "time_variable": time_variable,
    }, missing


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
        mag_name = f"{SWPC_MAG_FEED}-{span}.json"
        mag_url = f"{SWPC_BASE}/{role}/{mag_name}"
        urls.append(mag_url)
        try:
            mag_rows = load_json_url(mag_url, cache / role / mag_name)
            if isinstance(mag_rows, list):
                feeds[SWPC_MAG_FEED].extend(mag_rows)
        except Exception as exc:
            print(f"  error {mag_url}: {exc}")
        for feed in SWPC_EXIS_FEEDS:
            filename = f"{feed}-{span}.json"
            url = f"{SWPC_BASE}/{role}/{filename}"
            urls.append(url)
            try:
                rows = load_json_url(url, cache / role / filename)
                if isinstance(rows, list):
                    feeds[feed].extend(rows)
            except Exception as exc:
                print(f"  error {url}: {exc}")
    try:
        sources = load_json_url(
            f"{SWPC_BASE}/instrument-sources.json",
            cache / "instrument-sources.json",
        )
    except Exception:
        sources = []
    return feeds, urls, {"span": span, "instrument_sources": sources}


def swpc_records(
    feeds: dict,
    feed_names: tuple[str, ...],
    start: datetime,
    end: datetime,
    require_year_sat: bool = True,
) -> list[dict]:
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
                sat = int(row.get("satellite", -1))
            except (TypeError, ValueError):
                sat = -1
            if require_year_sat:
                try:
                    want = satellite_for_date(moment)
                except ValueError:
                    continue
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


def swpc_mag_records(rows: list, start: datetime, end: datetime) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
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
        buckets[key] = {
            "time": key,
            "satellite": f"g{sat}",
            "source": "swpc",
            **{
                name: numpy_to_json(value)
                for name, value in row.items()
                if name != "time_tag"
            },
        }
    return [buckets[ts] for ts in sorted(buckets)]


def _row_time(row: dict) -> datetime | None:
    raw = row.get("time_tag") or row.get("time")
    if not raw:
        return None
    return parse_iso(str(raw))


def records_from_rows(rows: list, start: datetime, end: datetime) -> list[dict]:
    out = []
    for row in rows or []:
        moment = _row_time(row)
        if moment is None or not (start <= moment <= end):
            continue
        rec = {
            name: numpy_to_json(value)
            for name, value in row.items()
            if name not in {"time_tag"}
        }
        rec["time"] = iso_z(moment)
        rec["source"] = rec.get("source") or "swpc"
        out.append(rec)
    out.sort(key=lambda item: item["time"])
    return out


def fetch_rtsw(start: datetime, end: datetime, cache_dir: Path) -> dict:
    cache = cache_dir / "swpc" / "rtsw"
    payload = {}
    for name in ("mag", "wind"):
        url = f"{RTSW_BASE}/rtsw_{name}_1m.json"
        print(f"RTSW {name}: {url}")
        try:
            rows = load_json_url(url, cache / f"rtsw_{name}_1m.json")
        except Exception as exc:
            print(f"  error {url}: {exc}")
            payload[name] = {"source": url, "records": [], "by_source": {}}
            continue
        native = records_from_rows(rows if isinstance(rows, list) else [], start, end)
        grouped: dict[str, list] = defaultdict(list)
        for rec in native:
            grouped[str(rec.get("source") or "unknown")].append(rec)
        by_source = {
            src: {
                "interval_minutes": OUTPUT_INTERVAL_MINUTES,
                "records": resample_records(recs, OUTPUT_INTERVAL_MINUTES),
            }
            for src, recs in grouped.items()
        }
        payload[name] = {
            "source": url,
            "interval_minutes": OUTPUT_INTERVAL_MINUTES,
            "records": resample_records(native, OUTPUT_INTERVAL_MINUTES),
            "by_source": by_source,
        }
    return payload


def fetch_swpc_index_series(
    url: str, start: datetime, end: datetime, cache_dir: Path, label: str
) -> dict:
    print(f"{label}: {url}")
    dest = cache_dir / "swpc" / Path(url).name
    try:
        rows = load_json_url(url, dest)
    except Exception as exc:
        print(f"  error {url}: {exc}")
        rows = []
    native = records_from_rows(rows if isinstance(rows, list) else [], start, end)
    return {
        "source": url,
        "interval_minutes": OUTPUT_INTERVAL_MINUTES,
        "records": resample_records(native, OUTPUT_INTERVAL_MINUTES),
    }


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
    parser.add_argument(
        "--mpsl",
        action="store_true",
        help="Также скачать NCEI L1b MPS-LO (~220 МБ/сутки).",
    )
    parser.add_argument(
        "--no-l1b",
        action="store_true",
        help="Не качать NCEI L1b (для длинных окон; L1b MPS-HI ~40 МБ/сутки).",
    )
    parser.add_argument(
        "--l1b-gaps",
        action="store_true",
        help="Качать L1b только за дни, где нет L2 avg5m.",
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
        print(f"SEISS {product}: SWPC 30-min + NCEI L1b (полные переменные) + L2 archive")
        realtime_5min = swpc_records(swpc_feeds, SWPC_FEEDS[product], start, end)
        realtime = resample_records(realtime_5min, OUTPUT_INTERVAL_MINUTES)
        ncei_payload, missing = fetch_seiss_product(
            product, start, end, args.cache, listing_cache
        )
        missing_files.extend(missing)
        if ncei_payload.get("records"):
            ncei_payload = dict(ncei_payload)
            ncei_payload["records"] = resample_records(
                ncei_payload["records"], OUTPUT_INTERVAL_MINUTES
            )
        if args.no_l1b:
            l1b_payload = {"records": [], "files": []}
        else:
            gap_dates = None
            if args.l1b_gaps:
                gap_dates = {
                    item["date"]
                    for item in missing
                    if item.get("product") == product and item.get("date")
                }
                print(f"  L1b gaps {product}: {len(gap_dates)} дней")
            l1b_payload, l1b_missing = fetch_l1b_product(
                product,
                start,
                end,
                args.cache,
                listing_cache,
                only_dates=gap_dates,
            )
            missing_files.extend(l1b_missing)
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
            "static": {},
            "instrument_sources": swpc_meta.get("instrument_sources"),
            "ncei": ncei_payload,
            "l1b": l1b_payload,
        }

    extra = list(L1B_EXTRA)
    if args.mpsl:
        extra.append("mpsl")
    if args.no_l1b:
        extra = []
    for extra_product in extra:
        print(f"SEISS {extra_product}: NCEI L1b")
        payload, missing = fetch_l1b_product(
            extra_product, start, end, args.cache, listing_cache
        )
        missing_files.extend(missing)
        seiss[extra_product] = payload

    print("SWPC GOES magnetometer (1-min → 30-min)")
    mag_5min = swpc_mag_records(swpc_feeds.get(SWPC_MAG_FEED, []), start, end)
    mag_30 = resample_records(mag_5min, OUTPUT_INTERVAL_MINUTES)
    seiss["magnetometer"] = {
        "satellite": list(dict.fromkeys(r.get("satellite") for r in mag_30 if r.get("satellite"))),
        "source": SWPC_BASE,
        "span": swpc_meta["span"],
        "interval_minutes": OUTPUT_INTERVAL_MINUTES,
        "records": mag_30,
        "records_native": mag_5min,
        "fields": ["He", "Hp", "Hn", "total", "arcjet_flag", "satellite"],
    }

    print("SWPC GOES EXIS (xrays + euvs, все спутники)")
    exis = {}
    for feed in SWPC_EXIS_FEEDS:
        native = swpc_records(
            swpc_feeds, (feed,), start, end, require_year_sat=False
        )
        exis[feed] = {
            "source": SWPC_BASE,
            "span": swpc_meta["span"],
            "interval_minutes": OUTPUT_INTERVAL_MINUTES,
            "records": resample_records(native, OUTPUT_INTERVAL_MINUTES),
        }

    solar_wind = fetch_rtsw(start, end, args.cache)
    swpc_kp = fetch_swpc_index_series(
        SWPC_KP_URL, start, end, args.cache, "SWPC estimated Kp"
    )
    dst = fetch_swpc_index_series(
        SWPC_DST_URL, start, end, args.cache, "SWPC Geospace Dst"
    )
    f107 = fetch_swpc_index_series(
        SWPC_F107_URL, start, end, args.cache, "SWPC F10.7"
    )

    result = {
        "fetched_at": iso_z(end),
        "window": {"start": iso_z(start), "end": iso_z(end)},
        "interval_minutes": OUTPUT_INTERVAL_MINUTES,
        "kp": geomagnetic,
        "seiss": seiss,
        "exis": exis,
        "solar_wind": solar_wind,
        "swpc_kp": swpc_kp,
        "dst": dst,
        "f107": f107,
        "missing_files": missing_files,
    }

    compact = (end - start) >= timedelta(days=14)
    out_path = args.out / "space_weather.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=None if compact else 2)

    print(
        f"Готово: {out_path} "
        f"(Hp30={len(geomagnetic['Hp30']['datetime'])}, "
        f"ap30={len(geomagnetic['ap30']['datetime'])}, "
        f"Kp={len(geomagnetic['Kp']['datetime'])}, "
        f"mpsh_swpc={len(seiss['mpsh']['records'])}, "
        f"sgps_swpc={len(seiss['sgps']['records'])}, "
        f"mpsh_l1b={len(seiss['mpsh']['l1b']['records'])}, "
        f"sgps_l1b={len(seiss['sgps']['l1b']['records'])}, "
        f"ehis_l1b={len(seiss.get('ehis', {}).get('records') or [])}, "
        f"mag={len(seiss['magnetometer']['records'])}, "
        f"xrays={len(exis['xrays']['records'])}, "
        f"euvs={len(exis['euvs']['records'])}, "
        f"rtsw_mag={len((solar_wind.get('mag') or {}).get('records') or [])}, "
        f"dst={len(dst['records'])}, "
        f"missing={len(missing_files)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
