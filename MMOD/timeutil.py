"""Datetime parsing, minute grids, and interval arithmetic."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from constants import MAX_WINDOW_SECONDS, STEP_SECONDS
from responses import WindowError


def iso_utc(dt: datetime) -> str:
    """Return ISO-8601 UTC string with Z suffix (no microseconds)."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_tca(tca_str: str) -> datetime | None:
    text = str(tca_str or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text.replace("+00:00", "Z"), fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_request_window(raw_start: str, raw_end: str) -> tuple[datetime, datetime]:
    try:
        start_time = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
    except ValueError as exc:
        raise WindowError(f"Invalid datetime format: {exc}") from exc

    if start_time.tzinfo is None or end_time.tzinfo is None:
        raise WindowError("start_time and end_time must include timezone information")

    start_time = start_time.astimezone(timezone.utc)
    end_time = end_time.astimezone(timezone.utc)

    if end_time <= start_time:
        raise WindowError("end_time must be after start_time")

    duration_seconds = (end_time - start_time).total_seconds()
    if duration_seconds > MAX_WINDOW_SECONDS:
        raise WindowError("Requested window exceeds maximum of 24 hours")

    return start_time, end_time


def minute_grid(start_time: datetime, end_time: datetime) -> list[datetime]:
    duration_seconds = (end_time - start_time).total_seconds()
    sample_count = math.ceil(duration_seconds / STEP_SECONDS)
    return [start_time + timedelta(seconds=i * STEP_SECONDS) for i in range(sample_count)]


def seconds_in_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[datetime]:
    """Inclusive-start exclusive-end seconds from merged intervals, unique and sorted."""
    stamps: list[datetime] = []
    seen: set[datetime] = set()
    for win_s, win_e in intervals:
        n = max(int((win_e - win_s).total_seconds()), 0)
        for i in range(n + 1):
            ts = win_s + timedelta(seconds=i)
            if ts > win_e:
                break
            if ts not in seen:
                seen.add(ts)
                stamps.append(ts)
    stamps.sort()
    return stamps


def merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or adjacent datetime intervals (sorted by start)."""
    if not intervals:
        return []
    sorted_ivs = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_ivs[0]]
    for start, end in sorted_ivs[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def union_duration_seconds(
    intervals: list[tuple[datetime, datetime]],
) -> int:
    """Total seconds covered by the union of intervals (no double-counting)."""
    merged = merge_intervals(intervals)
    return sum(int((e - s).total_seconds()) for s, e in merged)
