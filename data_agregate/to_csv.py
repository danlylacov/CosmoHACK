#!/usr/bin/env python3
"""
Превращает space_weather.json в CSV с равномерным шагом по времени.

Пример:
  python to_csv.py
  python to_csv.py --step 30 --input output/space_weather.json --out output/space_weather.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKIP_KEYS = {
    "time",
    "source",
    "interval_minutes",
    "n_samples",
    "satellite",
}


def parse_iso(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def floor_step(dt: datetime, minutes: int) -> datetime:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    step = minutes * 60
    epoch = int(dt.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % step), tz=timezone.utc)


def slug(text: str) -> str:
    text = str(text)
    text = text.replace(">=", "ge").replace(">", "gt")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_")


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def flatten(prefix: str, obj, out: dict) -> None:
    if obj is None:
        return
    if isinstance(obj, dict):
        if "energy" in obj or "channel" in obj or "line" in obj:
            label = obj.get("channel") or obj.get("energy") or obj.get("line") or "x"
            sat = obj.get("satellite")
            if sat is not None:
                label = f"g{sat}_{label}"
            rest = {
                key: val
                for key, val in obj.items()
                if key not in {"channel", "energy", "line", "satellite", "yaw_flip"}
            }
            if list(rest.keys()) == ["flux"]:
                flatten(f"{prefix}_{slug(label)}", rest["flux"], out)
            else:
                flatten(f"{prefix}_{slug(label)}", rest, out)
            return
        for key, val in obj.items():
            if key in SKIP_KEYS:
                continue
            flatten(f"{prefix}_{slug(key)}" if prefix else slug(key), val, out)
        return
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and (
            "energy" in obj[0] or "channel" in obj[0] or "line" in obj[0]
        ):
            for item in obj:
                flatten(prefix, item, out)
            return
        for idx, item in enumerate(obj):
            flatten(f"{prefix}_{idx}", item, out)
        return
    if isinstance(obj, bool):
        out[prefix] = int(obj)
        return
    out[prefix] = obj


def add_observation(store: dict, moment: datetime, prefix: str, record: dict) -> None:
    flat = {}
    flatten(prefix, record, flat)
    for col, value in flat.items():
        store.setdefault(col, []).append((moment, value))


def collect_series(data: dict) -> dict[str, list[tuple[datetime, object]]]:
    store: dict[str, list[tuple[datetime, object]]] = {}

    kp = data.get("kp") or {}
    for name, block in kp.items():
        if not isinstance(block, dict) or "datetime" not in block:
            continue
        times = block.get("datetime") or []
        values = block.get(name) or []
        statuses = block.get("status") or [None] * len(times)
        for ts, value, status in zip(times, values, statuses):
            moment = parse_iso(ts)
            store.setdefault(f"gfz_{name}", []).append((moment, value))
            if status is not None:
                store.setdefault(f"gfz_{name}_status", []).append((moment, status))

    seiss = data.get("seiss") or {}
    for product in ("mpsh", "sgps"):
        block = seiss.get(product) or {}
        for rec in block.get("records") or []:
            if not rec.get("time"):
                continue
            add_observation(store, parse_iso(rec["time"]), f"swpc_{product}", rec)
        for rec in (block.get("l1b") or {}).get("records") or []:
            if not rec.get("time"):
                continue
            add_observation(store, parse_iso(rec["time"]), f"l1b_{product}", rec)
        for rec in (block.get("ncei") or {}).get("records") or []:
            if not rec.get("time"):
                continue
            add_observation(store, parse_iso(rec["time"]), f"ncei_{product}", rec)

    for extra in ("ehis", "mpsl"):
        block = seiss.get(extra) or {}
        for rec in block.get("records") or []:
            if not rec.get("time"):
                continue
            add_observation(store, parse_iso(rec["time"]), f"l1b_{extra}", rec)

    mag = seiss.get("magnetometer") or {}
    for rec in mag.get("records") or []:
        if not rec.get("time"):
            continue
        add_observation(store, parse_iso(rec["time"]), "swpc_mag", rec)

    exis = data.get("exis") or {}
    for name, block in exis.items():
        if not isinstance(block, dict):
            continue
        for rec in block.get("records") or []:
            if not rec.get("time"):
                continue
            add_observation(store, parse_iso(rec["time"]), f"swpc_{name}", rec)

    solar_wind = data.get("solar_wind") or {}
    for kind, block in solar_wind.items():
        if not isinstance(block, dict):
            continue
        by_source = block.get("by_source") or {}
        if by_source:
            for src, src_block in by_source.items():
                for rec in (src_block or {}).get("records") or []:
                    if not rec.get("time"):
                        continue
                    add_observation(
                        store,
                        parse_iso(rec["time"]),
                        f"rtsw_{kind}_{slug(src)}",
                        rec,
                    )
        else:
            for rec in block.get("records") or []:
                if not rec.get("time"):
                    continue
                add_observation(store, parse_iso(rec["time"]), f"rtsw_{kind}", rec)

    for name in ("swpc_kp", "dst", "f107"):
        block = data.get(name) or {}
        for rec in block.get("records") or []:
            if not rec.get("time"):
                continue
            add_observation(store, parse_iso(rec["time"]), name, rec)

    return store


def reduce_bin(values: list):
    nums = [float(v) for v in values if is_number(v)]
    if nums and len(nums) == len(values):
        return sum(nums) / len(nums)
    return values[-1]


def build_grid(start: datetime, end: datetime, minutes: int):
    t = floor_step(start, minutes)
    last = floor_step(end, minutes)
    delta = timedelta(minutes=minutes)
    while t <= last:
        yield t
        t += delta


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать все ряды из space_weather.json в CSV с заданным шагом."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "space_weather.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output" / "space_weather.csv",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=0,
        help="Шаг в минутах. 0 = взять interval_minutes из JSON (обычно 30).",
    )
    parser.add_argument(
        "--no-ffill",
        action="store_true",
        help="Не протягивать даже редкие индексы GFZ.",
    )
    parser.add_argument(
        "--ffill-all",
        action="store_true",
        help="Протягивать все колонки, включая частицы (скрывает дыры архива).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    print(f"reading {args.input} …", flush=True)
    data = json.loads(args.input.read_text(encoding="utf-8"))
    for block in (data.get("seiss") or {}).values():
        if isinstance(block, dict):
            block.pop("records_5min", None)
            if isinstance(block.get("ncei"), dict):
                block["ncei"].pop("records_5min", None)
    window = data.get("window") or {}
    start = parse_iso(window["start"])
    end = parse_iso(window["end"])
    step = args.step or int(data.get("interval_minutes") or 30)
    if step <= 0:
        raise SystemExit("--step должен быть > 0")

    print(f"JSON loaded, flattening…", flush=True)
    series = collect_series(data)
    del data
    columns = sorted(series)
    print(f"series={len(columns)}  building grid step={step}min", flush=True)
    last_seen = {col: None for col in columns}
    binned = {col: defaultdict(list) for col in columns}
    for col in columns:
        for ts, val in series[col]:
            binned[col][floor_step(ts, step)].append(val)
    del series
    rows = []
    delta = timedelta(minutes=step)

    ffill_all = args.ffill_all and not args.no_ffill
    ffill_slow = not args.no_ffill

    def allow_ffill(col: str) -> bool:
        if ffill_all:
            return True
        if not ffill_slow:
            return False
        return col.startswith("gfz_") or col.startswith("f107_")

    for t in build_grid(start, end, step):
        row = {"time": iso_z(t)}
        for col in columns:
            vals = binned[col].get(t)
            if vals:
                value = reduce_bin(vals)
                last_seen[col] = value
            elif allow_ffill(col):
                value = last_seen[col]
            else:
                value = None
            row[col] = value
        rows.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["time", *columns]
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    filled = 0
    if rows:
        filled = sum(1 for v in rows[-1].values() if v is not None) - 1
    print(
        f"Готово: {args.out}  rows={len(rows)} cols={len(columns)} "
        f"step={step}min  last_row_filled={filled}/{len(columns)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
