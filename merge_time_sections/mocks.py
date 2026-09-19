"""OpenAPI-shaped fixtures on 2026-09-18; envelope follows the requested window."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from .timeutil import iso_z

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def mock_weather(start: datetime, end: datetime) -> dict:
    payload = copy.deepcopy(_load("space_weather.json"))
    payload["start"] = iso_z(start)
    payload["end"] = iso_z(end)
    return payload


def mock_distances(
    start: datetime,
    end: datetime,
    *,
    critical_distance_km: float | None = None,
) -> dict:
    payload = copy.deepcopy(_load("distances.json"))
    payload["request"]["start_time"] = iso_z(start)
    payload["request"]["end_time"] = iso_z(end)
    payload["request"]["critical_distance_km"] = critical_distance_km
    if critical_distance_km is None:
        for sample in payload["samples"]:
            sample["is_critical"] = None
        payload["critical_intervals"] = []
        payload["summary"]["critical_duration_seconds"] = 0
    return payload
