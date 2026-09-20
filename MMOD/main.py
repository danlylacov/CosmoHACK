#!/usr/bin/env python3
"""
CosmoHACK Orbit API — FastAPI app.

Endpoints:
  GET  /health
  POST /api/v1/orbits/positions
  POST /api/v1/conjunctions/distances
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# ── resolve sibling imports ───────────────────────────────────────────────────
_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

from caches import (
    cache_lock,
    invalidate_orbit_caches,
    omm_cache,
    satcat_cache,
    socrates_cache,
)
from constants import LOGGER_NAME
from distances import compute_distances
from models import ConjunctionRequest, PositionsRequest
from positions import compute_positions
from responses import ApiError, WindowError, api_error_response, error_response, json_response
from timeutil import parse_request_window

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(LOGGER_NAME)

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="CosmoHACK Orbit API", version="1.0.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/v1/orbits/positions")
async def positions(body: PositionsRequest):
    try:
        start_time, end_time = parse_request_window(body.start_time, body.end_time)
    except WindowError as exc:
        return error_response(422, "VALIDATION_ERROR", str(exc))

    try:
        payload = await compute_positions(start_time, end_time)
    except ApiError as exc:
        return api_error_response(exc)
    return json_response(payload)


@app.post("/api/v1/conjunctions/distances")
async def distances(body: ConjunctionRequest):
    try:
        start_time, end_time = parse_request_window(body.start_time, body.end_time)
    except WindowError as exc:
        return error_response(422, "VALIDATION_ERROR", str(exc))

    critical_km = body.critical_distance_km
    if critical_km is not None and critical_km <= 0:
        return error_response(422, "VALIDATION_ERROR", "critical_distance_km must be > 0")

    try:
        payload = await compute_distances(start_time, end_time, critical_km)
    except ApiError as exc:
        return api_error_response(exc)
    return json_response(payload)


# Re-exported for callers that imported cache helpers from main.
__all__ = [
    "app",
    "cache_lock",
    "invalidate_orbit_caches",
    "log",
    "omm_cache",
    "satcat_cache",
    "socrates_cache",
]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
