"""OpenAPI-shaped fixtures; timestamps shift onto the requested window."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .timeutil import iso_z, parse_utc

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
FIXTURE_ORIGIN = parse_utc("2026-09-18T10:00:00Z")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _shift(value, delta: timedelta):
    if isinstance(value, dict):
        return {key: _shift(item, delta) for key, item in value.items()}
    if isinstance(value, list):
        return [_shift(item, delta) for item in value]
    if isinstance(value, str) and "T" in value:
        try:
            return iso_z(parse_utc(value) + delta)
        except ValueError:
            return value
    return value


def mock_weather(start: datetime, end: datetime) -> dict:
    payload = _shift(_load("space_weather.json"), start - FIXTURE_ORIGIN)
    payload["start"] = iso_z(start)
    payload["end"] = iso_z(end)
    return payload


def mock_distances(
    start: datetime,
    end: datetime,
    *,
    critical_distance_km: float | None = None,
) -> dict:
    payload = _shift(_load("distances.json"), start - FIXTURE_ORIGIN)
    payload["request"]["start_time"] = iso_z(start)
    payload["request"]["end_time"] = iso_z(end)
    payload["request"]["critical_distance_km"] = critical_distance_km
    if critical_distance_km is None:
        for sample in payload["samples"]:
            sample["is_critical"] = None
        payload["critical_intervals"] = []
        payload["summary"]["critical_duration_seconds"] = 0
    return payload
