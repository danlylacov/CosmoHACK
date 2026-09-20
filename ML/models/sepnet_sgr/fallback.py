"""Dependency-free, causal physical-value statistics for inference fallback."""

import csv
import hashlib
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

_METRICS = [f"proton_flux_above_{energy}_mev" for energy in (10, 50, 100)] + ["solar_xray_flux_long"]
TARGETS = [name for metric in _METRICS for name in (metric, metric + "_max")] + ["geomagnetic_kp"]
OUTPUTS = [f"{metric}_{stat}" for metric in _METRICS for stat in ("mean", "max")] + ["geomagnetic_kp"]
_STEP = timedelta(minutes=30)
_CONFLICT = object()


def parse_time(value):
    """Parse an ISO timestamp, treating a missing timezone as UTC."""
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def floor_time(value, minutes=30):
    """Floor a time to UTC intervals; supports 180-minute Kp boundaries."""
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 1:
        raise ValueError("minutes must be a positive integer")
    value = parse_time(value)
    epoch, step = datetime(1970, 1, 1, tzinfo=timezone.utc), timedelta(minutes=minutes)
    return epoch + ((value - epoch) // step) * step


def _number(value, column):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if (not isinstance(value, bool) and math.isfinite(number)
                      and number >= 0 and (column != 8 or number <= 9)) else None


def _merge(observations, time, values):
    """Missing cells complement duplicates; conflicting target cells stay unknown."""
    try:
        time, values = parse_time(time), list(values)
        time + _STEP  # Reject timestamps whose completed-window end cannot be represented.
    except (TypeError, ValueError, OverflowError):
        return
    if time != floor_time(time):
        return
    row = observations.setdefault(time, [None] * 9)
    for column, value in enumerate(values[:9]):
        value = _number(value, column)
        if value is None or row[column] is _CONFLICT:
            continue
        if row[column] is None:
            row[column] = value
        elif row[column] != value:
            row[column] = _CONFLICT


def _statistics(observations):
    counts, means, sums = [0] * 9, [0.0] * 9, [0.0] * 9
    last, ends, blocks = [None] * 9, [None] * 9, {}

    def add(column, value, end):
        if value is None or value is _CONFLICT:
            return
        counts[column] += 1
        delta = value - means[column]
        means[column] += delta / counts[column]
        sums[column] += delta * (value - means[column])
        last[column], ends[column] = value, end

    for time, row in sorted(observations.items()):
        for column in range(8):
            add(column, row[column], time + _STEP)
        blocks.setdefault(floor_time(time, 180), {})[time] = row[8]
    for start, rows in sorted(blocks.items()):
        values = [rows.get(start + slot * _STEP) for slot in range(6)]
        if all(value is not None and value is not _CONFLICT for value in values) and len(set(values)) == 1:
            add(8, values[0], start + timedelta(hours=3))
    variance = [max(0.0, sums[j] / counts[j]) if counts[j] and math.isfinite(sums[j]) else None
                for j in range(9)]
    data_end = max((end for end in ends if end is not None), default=None)
    return {"last": last, "variance": variance,
            "observed_end": [end.isoformat() if end else None for end in ends],
            "data_end_utc": data_end.isoformat() if data_end else None}


def statistics_rows(times, rows):
    """Population statistics for supplied physical training rows; no file dependencies.

    Each valid flux target contributes once per timestamp; Kp once per complete,
    consistent UTC three-hour interval. No future data is added or filled.
    """
    observations = {}
    for time, values in zip(times, rows):
        _merge(observations, time, values)
    return _statistics(observations)


def csv_statistics(data, cutoff):
    """Read a path or list of paths, tolerating malformed files/cells without future rows."""
    cutoff, observations, sources = parse_time(cutoff), {}, []
    files = []
    for item in data if isinstance(data, (list, tuple)) else [data]:
        try:
            path = Path(item).expanduser()
            files.extend([path] if path.is_file() else sorted(path.rglob("*.csv")))
        except (OSError, TypeError, ValueError):
            continue
    files = list(dict.fromkeys(files))
    for file in files:
        digest, local = hashlib.sha256(), {}
        try:
            with file.open("rb") as stream:
                def lines():
                    for line in stream:
                        digest.update(line)
                        yield line.decode("utf-8-sig", errors="replace")

                reader = csv.DictReader(lines())
                fields = reader.fieldnames or []
                duplicate = {name for name in fields if fields.count(name) > 1}
                if "time_utc" not in fields or "time_utc" in duplicate:
                    continue
                while True:
                    try:
                        row = next(reader)
                    except StopIteration:
                        break
                    except csv.Error:
                        continue
                    try:
                        time = parse_time(row.get("time_utc"))
                        end = time + _STEP
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if end <= cutoff:
                        _merge(local, time, [row.get(name) if name not in duplicate else None for name in TARGETS])
            source = {"path": str(file.resolve()), "sha256": digest.hexdigest()}
        except (OSError, csv.Error, TypeError, ValueError, OverflowError):
            continue
        for time, values in local.items():
            # Preserve conflict markers while merging different source files.
            _merge(observations, time, [None if value is _CONFLICT else value for value in values])
            for column, value in enumerate(values):
                if value is _CONFLICT:
                    observations[time][column] = _CONFLICT
        sources.append(source)
    result = _statistics(observations)
    result["sources"] = sources
    return result
