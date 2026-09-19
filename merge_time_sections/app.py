#!/usr/bin/env python3
"""CosmoHACK EVA ranking service.

Standalone FastAPI app. Does not propagate orbits: it calls the Orbit API
(POST /api/v1/conjunctions/distances) and optionally the weather API, then
ranks EVA windows via rank_eva_windows.

Run (from CosmoHACK/):
  uvicorn merge_time_sections.app:app --host 0.0.0.0 --port 8001

Orbit API (MMOD) must already be on CONJUNCTION_API_BASE_URL (default :8000).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
_ROOT = _DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from merge_time_sections.clients import (
    CONJUNCTION_API_BASE_URL,
    SPACE_WEATHER_BASE_URL,
    UpstreamError,
    fetch_distances_async,
    fetch_weather_async,
)
from merge_time_sections.rank import rank_eva_windows
from merge_time_sections.timeutil import iso_z

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("eva_api")

DISTANCES_CACHE_TTL_SECONDS = int(os.environ.get("DISTANCES_CACHE_TTL_SECONDS", str(2 * 60 * 60)))
WEATHER_ENABLED = os.environ.get("WEATHER_ENABLED", "0") not in {"0", "false", "False", ""}

_distances_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = asyncio.Lock()


class EvaWindowsRequest(BaseModel):
    start_time: str
    end_time: str
    duration_min: int = Field(..., gt=0)
    step_min: int = Field(30, gt=0)
    top_k: int = Field(5, ge=2)
    critical_distance_km: float = Field(..., gt=0, le=5)

    model_config = {"json_schema_extra": {
        "example": {
            "start_time": "2026-09-20T03:30:00Z",
            "end_time": "2026-09-20T05:30:00Z",
            "duration_min": 30,
            "step_min": 1,
            "top_k": 5,
            "critical_distance_km": 5.0,
        }
    }}


app = FastAPI(
    title="CosmoHACK EVA API",
    version="1.0.0",
    description=(
        "Ranks EVA time windows of fixed duration. Distances come from the "
        "Orbit API (MMOD). Weather/ML is optional and unused until WEATHER_ENABLED=1."
    ),
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _err(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _ok(payload: dict) -> JSONResponse:
    return JSONResponse(content=payload)


def _parse_time_window(raw_start: str, raw_end: str) -> tuple[datetime, datetime] | JSONResponse:
    try:
        start_time = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
    except ValueError as exc:
        return _err(422, "VALIDATION_ERROR", f"Invalid datetime format: {exc}")
    if start_time.tzinfo is None or end_time.tzinfo is None:
        return _err(422, "VALIDATION_ERROR",
                    "start_time and end_time must include timezone information")
    start_time = start_time.astimezone(timezone.utc)
    end_time = end_time.astimezone(timezone.utc)
    if end_time <= start_time:
        return _err(422, "VALIDATION_ERROR", "end_time must be after start_time")
    if (end_time - start_time).total_seconds() > 86400:
        return _err(422, "VALIDATION_ERROR",
                    "Requested window exceeds maximum of 24 hours")
    return start_time, end_time


def _upstream_response(exc: UpstreamError) -> JSONResponse:
    payload = exc.payload
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return JSONResponse(status_code=exc.status_code, content=payload)
    code = "SOURCE_UNAVAILABLE" if exc.status_code >= 500 else "UPSTREAM_ERROR"
    return _err(exc.status_code if exc.status_code >= 400 else 502, code, str(exc))


async def _cached_distances(
    start: datetime,
    end: datetime,
    critical_km: float,
) -> tuple[dict, bool]:
    key = (iso_z(start), iso_z(end), float(critical_km))
    now = time.monotonic()
    async with _cache_lock:
        hit = _distances_cache.get(key)
        if hit is not None:
            stored_at, body = hit
            if now - stored_at < DISTANCES_CACHE_TTL_SECONDS:
                log.info("distances cache hit — skip Orbit API")
                return body, True
            _distances_cache.pop(key, None)

    log.info("distances cache miss — POST %s", CONJUNCTION_API_BASE_URL)
    body = await fetch_distances_async(start, end, critical_distance_km=critical_km)
    async with _cache_lock:
        _distances_cache[key] = (time.monotonic(), body)
    return body, False


async def _load_weather(start: datetime, end: datetime) -> tuple[dict, bool]:
    """Return (weather_payload, used). Empty records if weather is off or down."""
    empty = {"records": []}
    if not WEATHER_ENABLED:
        return empty, False
    try:
        payload = await fetch_weather_async(start, end)
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            log.warning("weather payload has no records[] — ignoring")
            return empty, False
        return payload, True
    except Exception as exc:
        log.warning("weather unavailable (%s): %s", SPACE_WEATHER_BASE_URL, exc)
        return empty, False


@app.get("/health")
async def health():
    return {"status": "ok", "service": "eva"}


@app.post("/api/v1/eva/windows")
async def eva_windows(body: EvaWindowsRequest):
    parsed = _parse_time_window(body.start_time, body.end_time)
    if isinstance(parsed, JSONResponse):
        return parsed
    start_time, end_time = parsed

    if body.duration_min <= 0:
        return _err(422, "VALIDATION_ERROR", "duration_min must be > 0")
    if body.step_min <= 0:
        return _err(422, "VALIDATION_ERROR", "step_min must be > 0")
    if body.top_k < 2:
        return _err(422, "VALIDATION_ERROR", "top_k must be >= 2")
    if body.critical_distance_km <= 0 or body.critical_distance_km > 5:
        return _err(
            422,
            "VALIDATION_ERROR",
            "critical_distance_km must be > 0 and <= 5",
        )

    horizon_min = int((end_time - start_time).total_seconds() // 60)
    if body.duration_min > horizon_min:
        return _err(
            422,
            "VALIDATION_ERROR",
            f"duration_min ({body.duration_min}) exceeds request horizon ({horizon_min} minutes)",
        )

    last_start = horizon_min - body.duration_min
    n_starts = len(range(0, last_start + 1, body.step_min)) if last_start >= 0 else 0
    if n_starts < 2:
        return _err(
            422,
            "VALIDATION_ERROR",
            "Заданный горизонт, длительность и шаг не позволяют сравнить два окна одинаковой длительности.",
        )

    try:
        distance_bundle, cache_hit = await _cached_distances(
            start_time, end_time, body.critical_distance_km,
        )
    except UpstreamError as exc:
        log.warning("Orbit API failed: %s", exc)
        return _upstream_response(exc)
    except Exception as exc:
        log.exception("Orbit API call failed")
        return _err(503, "SOURCE_UNAVAILABLE", f"Orbit API unavailable: {exc}")

    weather, weather_used = await _load_weather(start_time, end_time)

    bundle = {
        "start": iso_z(start_time),
        "end": iso_z(end_time),
        "weather": weather if weather_used else {"records": []},
        "distances": distance_bundle,
    }
    try:
        ranked = rank_eva_windows(
            bundle,
            d=body.duration_min,
            step_min=body.step_min,
            top_k=body.top_k,
            d_crit_km=body.critical_distance_km,
        )
    except ValueError as exc:
        return _err(422, "VALIDATION_ERROR", str(exc))

    dq_src = distance_bundle.get("data_quality") or {}
    warnings = list(dq_src.get("warnings") or [])
    if not weather_used:
        warnings.append("Погодные факторы пока не участвовали в расчёте.")
    if ranked.get("unknown_minutes"):
        warnings.append("Часть минут горизонта имеет неизвестные расстояния.")
    if ranked.get("no_candidates") or dq_src.get("status") == "NO_CANDIDATES":
        warnings.append(
            "SOCRATES не обнаружил отслеживаемых сближений в пределах области скрининга. "
            "Это не абсолютная гарантия безопасности."
        )
    if ranked.get("safe_count", 0) < 2:
        warnings.append(
            "Невозможно сформировать несколько полностью безопасных окон заданной длительности."
        )
    if ranked.get("safe_count", 0) == 0:
        warnings.append(
            "Полностью безопасные окна заданной длительности не найдены. "
            "Все предложенные варианты требуют дополнительной проверки."
        )
    elif ranked.get("windows") and all(
        w.get("status") in {"REQUIRES_REVIEW", "INSUFFICIENT_DATA", "CAUTION"}
        for w in ranked["windows"]
    ):
        warnings.append("Все доступные варианты требуют проверки.")

    dq = {
        "status": dq_src.get("status"),
        "warnings": warnings,
        "elements_epoch": dq_src.get("elements_epoch"),
        "calculated_at": dq_src.get("calculated_at"),
    }
    payload = {
        "request": {
            "start_time": iso_z(start_time),
            "end_time": iso_z(end_time),
            "duration_min": body.duration_min,
            "step_min": body.step_min,
            "top_k": body.top_k,
            "critical_distance_km": body.critical_distance_km,
        },
        "horizon_minutes": ranked["n"],
        "windows": ranked["windows"],
        "data_quality": dq,
    }
    log.info(
        "[eva/windows] duration=%d step=%d top_k=%d returned=%d horizon=%d "
        "safe=%d distances_cache=%s weather=%s status=%s",
        body.duration_min, body.step_min, body.top_k,
        len(ranked["windows"]), ranked["n"],
        ranked.get("safe_count", 0), cache_hit, weather_used,
        dq.get("status"),
    )
    return _ok(payload)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run("merge_time_sections.app:app", host="0.0.0.0", port=port, reload=False)
