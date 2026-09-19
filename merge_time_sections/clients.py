"""HTTP clients for space weather and ISS conjunction distances."""

from __future__ import annotations

import os
from datetime import datetime

from .timeutil import iso_z

SPACE_WEATHER_BASE_URL = os.environ.get(
    "SPACE_WEATHER_BASE_URL", "http://127.0.0.1:8002"
)
CONJUNCTION_API_BASE_URL = os.environ.get(
    "CONJUNCTION_API_BASE_URL", "http://127.0.0.1:8000"
)
WEATHER_PATH = os.environ.get("SPACE_WEATHER_PATH", "/space-weather")
DISTANCES_PATH = "/api/v1/conjunctions/distances"
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "120"))


class UpstreamError(RuntimeError):
    """Non-success response from an upstream API."""

    def __init__(self, source: str, status_code: int, body: str, payload: dict | None = None) -> None:
        self.source = source
        self.status_code = status_code
        self.body = body
        self.payload = payload
        super().__init__(f"{source} HTTP {status_code}: {body}")


def _httpx():
    import httpx

    return httpx


def _check(source: str, response) -> dict:
    if response.status_code != 200:
        payload = None
        try:
            payload = response.json()
        except Exception:
            payload = None
        raise UpstreamError(source, response.status_code, response.text, payload)
    return response.json()


def fetch_weather(
    start: datetime,
    end: datetime,
    *,
    base_url: str | None = None,
    client=None,
) -> dict:
    url = (base_url or SPACE_WEATHER_BASE_URL).rstrip("/") + WEATHER_PATH
    params = {"start": iso_z(start), "end": iso_z(end)}
    if client is not None:
        return _check("weather", client.get(url, params=params))
    with _httpx().Client(timeout=UPSTREAM_TIMEOUT) as owned:
        return _check("weather", owned.get(url, params=params))


def fetch_distances(
    start: datetime,
    end: datetime,
    *,
    critical_distance_km: float | None = None,
    base_url: str | None = None,
    client=None,
) -> dict:
    url = (base_url or CONJUNCTION_API_BASE_URL).rstrip("/") + DISTANCES_PATH
    payload = {
        "start_time": iso_z(start),
        "end_time": iso_z(end),
        "critical_distance_km": critical_distance_km,
    }
    if client is not None:
        return _check("distances", client.post(url, json=payload))
    with _httpx().Client(timeout=UPSTREAM_TIMEOUT) as owned:
        return _check("distances", owned.post(url, json=payload))


async def fetch_weather_async(
    start: datetime,
    end: datetime,
    *,
    base_url: str | None = None,
    client=None,
) -> dict:
    httpx = _httpx()
    url = (base_url or SPACE_WEATHER_BASE_URL).rstrip("/") + WEATHER_PATH
    params = {"start": iso_z(start), "end": iso_z(end)}
    if client is not None:
        return _check("weather", await client.get(url, params=params))
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as owned:
        return _check("weather", await owned.get(url, params=params))


async def fetch_distances_async(
    start: datetime,
    end: datetime,
    *,
    critical_distance_km: float | None = None,
    base_url: str | None = None,
    client=None,
) -> dict:
    httpx = _httpx()
    url = (base_url or CONJUNCTION_API_BASE_URL).rstrip("/") + DISTANCES_PATH
    payload = {
        "start_time": iso_z(start),
        "end_time": iso_z(end),
        "critical_distance_km": critical_distance_km,
    }
    if client is not None:
        return _check("distances", await client.post(url, json=payload))
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as owned:
        return _check("distances", await owned.post(url, json=payload))
