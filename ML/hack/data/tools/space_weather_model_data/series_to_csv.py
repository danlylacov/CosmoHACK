#!/usr/bin/env python3
"""Ряды из result.json: окна по 30 минут и статистики частых измерений."""
import argparse
from bisect import bisect_right
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

STEP = timedelta(minutes=30)
INTERVAL_SECONDS = {"geomagnetic_kp": 10800, "geomagnetic_ap": 10800}


def utc(value):
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return time.replace(tzinfo=time.tzinfo or timezone.utc).astimezone(timezone.utc)


def floor(time):
    return time.replace(minute=time.minute // 30 * 30, second=0, microsecond=0)


def window_stats(points):
    if not points:
        return "", "", "", 0
    values = [value for _, value in points]
    last_time = max(time for time, _ in points)
    last = [value for time, value in points if time == last_time]
    return min(values), max(values), sum(last) / len(last), len(values)


def output_ranges(period, start, end):
    """Only accumulated periods carry a sparse, half-hour-aligned range list."""
    if "included_ranges_utc" not in period:
        return [(floor(start), end)]
    ranges = []
    for item in period["included_ranges_utc"]:
        left, right = utc(item["start_utc"]), utc(item["end_exclusive_utc"])
        if right <= left or left != floor(left) or right != floor(right):
            raise ValueError("included_ranges_utc: ожидаются положительные интервалы с шагом 30 минут")
        left, right = max(left, floor(start)), min(right, end)
        if left < right:
            ranges.append((left, right))
    merged = []
    for left, right in sorted(ranges):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def convert(data, output):
    period = data["period"]
    start = utc(period["start_utc"])
    end = utc(period["available_until_exclusive_utc"])
    if end <= start:
        raise ValueError("Конец периода должен быть позже начала")
    ranges = output_ranges(period, start, end)
    starts = [left for left, _ in ranges]

    def included(bucket):
        index = bisect_right(starts, bucket) - 1
        return index >= 0 and bucket < ranges[index][1]

    metrics = sorted({series["metric"] for series in data["series"]})
    rows = defaultdict(lambda: defaultdict(list))
    points = defaultdict(lambda: defaultdict(list))
    fast_metrics = set()
    for series in data["series"]:
        metric = series["metric"]
        bins, seen = defaultdict(list), set()
        for sample in series["samples"]:
            value = sample["value"]
            time = utc(sample["time_utc"])
            quality = sample.get("quality")
            quality = quality if isinstance(quality, dict) else {}
            seconds = quality.get("interval_seconds", INTERVAL_SECONDS.get(metric, 0))
            stop = utc(quality["interval_end_utc"]) if quality.get("interval_end_utc") else time + timedelta(seconds=seconds)
            fast = quality.get("original_interval_seconds", (stop - time).total_seconds()) < STEP.total_seconds()
            if fast:
                fast_metrics.add(metric)
            if value is None or not math.isfinite(value):
                continue
            key = (time, stop, value)
            if key in seen:
                continue
            seen.add(key)
            if stop - time > STEP:
                # Повторяем только внутри известного интервала, не до следующей точки.
                if time >= end or stop <= start:
                    continue
                bucket, stop = floor(max(time, start)), min(stop, end)
            elif start <= time < end:
                bucket = floor(time)
                stop = bucket + STEP
                if fast and included(bucket):
                    points[bucket][metric].append((time, value))
            else:
                continue
            while bucket < stop:
                if included(bucket):
                    bins[bucket].append(value)
                bucket += STEP
        for time, values in bins.items():
            rows[time][metric].append(sum(values) / len(values))

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        fields = ["time_utc"]
        for metric in metrics:
            fields.append(metric)
            if metric in fast_metrics:
                fields.extend(metric + "_" + stat for stat in ("min", "max", "last", "count"))
        writer.writerow(fields)
        for left, right in ranges:
            time = left
            while time < right:
                row = [time.isoformat().replace("+00:00", "Z")]
                for metric in metrics:
                    values = rows[time][metric]
                    row.append(sum(values) / len(values) if values else "")
                    if metric in fast_metrics:
                        row.extend(window_stats(points[time][metric]))
                writer.writerow(row)
                time += STEP


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="result.json загрузчика")
    parser.add_argument("output", type=Path, help="путь к series.csv")
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as stream:
        convert(json.load(stream), args.output)
