"""Fetch weather and ISS distances for one UTC window, then rank EVA slots."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .clients import (
    CONJUNCTION_API_BASE_URL,
    SPACE_WEATHER_BASE_URL,
    UpstreamError,
    fetch_distances,
    fetch_weather,
)
from .mocks import mock_distances, mock_weather
from .rank import rank_eva_windows
from .timeutil import IntervalError, iso_z, parse_utc, validate_window

__all__ = [
    "CONJUNCTION_API_BASE_URL",
    "SPACE_WEATHER_BASE_URL",
    "IntervalError",
    "UpstreamError",
    "fetch_interval",
    "rank_eva_windows",
]


def fetch_interval(
    start: datetime | str,
    end: datetime | str,
    *,
    use_mock: bool = True,
    critical_distance_km: float | None = None,
    weather_base_url: str | None = None,
    conjunction_base_url: str | None = None,
) -> dict:
    """Return weather and distances for the same `[start, end]` UTC interval."""
    start_utc = parse_utc(start)
    end_utc = parse_utc(end)
    validate_window(start_utc, end_utc)

    if use_mock:
        weather = mock_weather(start_utc, end_utc)
        distances = mock_distances(
            start_utc, end_utc, critical_distance_km=critical_distance_km
        )
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            weather_future = pool.submit(
                fetch_weather,
                start_utc,
                end_utc,
                base_url=weather_base_url,
            )
            distances_future = pool.submit(
                fetch_distances,
                start_utc,
                end_utc,
                critical_distance_km=critical_distance_km,
                base_url=conjunction_base_url,
            )
            weather = weather_future.result()
            distances = distances_future.result()

    return {
        "start": iso_z(start_utc),
        "end": iso_z(end_utc),
        "weather": weather,
        "distances": distances,
    }
