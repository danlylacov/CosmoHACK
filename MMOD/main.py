#!/usr/bin/env python3
"""
CosmoHACK Orbit API — FastAPI app.

Endpoints:
  GET  /health
  POST /api/v1/orbits/positions
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from sgp4 import omm as sgp4_omm
from sgp4.api import jday, Satrec

# ── resolve sibling imports ───────────────────────────────────────────────────
_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

from get_data import fetch_iss_conjunctions

# ── optional orjson ───────────────────────────────────────────────────────────
try:
    import orjson
    from fastapi.responses import ORJSONResponse as _JSONResponse
    _HAS_ORJSON = True
except ImportError:
    from fastapi.responses import JSONResponse as _JSONResponse
    _HAS_ORJSON = False

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("orbit_api")

# ── constants ─────────────────────────────────────────────────────────────────
ISS_NORAD = "25544"
CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
USER_AGENT = "Mozilla/5.0 (compatible; CosmoHACK-OrbitAPI/1.0)"
SOCRATES_CACHE_TTL_SECONDS = 2 * 60 * 60
ORBITAL_ELEMENTS_CACHE_TTL_SECONDS = 2 * 60 * 60
OMM_TIMEOUT = 30.0
MAX_CONCURRENT_OMM = 5

# ── module-level cache ────────────────────────────────────────────────────────
# socrates_cache: {"events": list | None, "retrieved_at": datetime | None}
socrates_cache: dict[str, Any] = {"events": None, "retrieved_at": None}

# omm_cache: {norad_id_str: {"satrec": Satrec, "record": dict, "name": str,
#                             "epoch": datetime, "retrieved_at": datetime}}
omm_cache: dict[str, dict] = {}

cache_lock = asyncio.Lock()

# ── request model ────────────────────────────────────────────────────────────

class PositionsRequest(BaseModel):
    start_time: str
    end_time: str

    model_config = {"json_schema_extra": {
        "example": {
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:03:00Z",
        }
    }}


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="CosmoHACK Orbit API", version="1.0.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ── helpers: error responses ──────────────────────────────────────────────────

def _err(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


# ── helpers: time formatting ──────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    """Return ISO-8601 UTC string with Z suffix."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── SOCRATES cache ────────────────────────────────────────────────────────────

async def _get_socrates_events() -> tuple[list[dict], datetime]:
    """
    Return (events, retrieved_at).
    Uses in-memory cache with SOCRATES_CACHE_TTL_SECONDS TTL.
    Falls back to stale cache on error with a warning.
    Raises RuntimeError when no data at all is available.
    """
    now = datetime.now(timezone.utc)

    async with cache_lock:
        cached_events = socrates_cache["events"]
        cached_at = socrates_cache["retrieved_at"]

        age = (now - cached_at).total_seconds() if cached_at else float("inf")

        if cached_at and age < SOCRATES_CACHE_TTL_SECONDS:
            unique_count = len(dict.fromkeys(
                e["other_norad"] for e in cached_events if e.get("other_norad")
            ))
            log.info("SOCRATES cache hit (age %.0fs, %d events, %d unique candidates)",
                     age, len(cached_events), unique_count)
            return cached_events, cached_at

        log.info("SOCRATES cache miss — fetching from CelesTrak SOCRATES")
        try:
            events = await asyncio.to_thread(fetch_iss_conjunctions, "MINRANGE", 25)
            socrates_cache["events"] = events
            socrates_cache["retrieved_at"] = now
            unique_count = len(dict.fromkeys(
                e["other_norad"] for e in events if e.get("other_norad")
            ))
            log.info("SOCRATES fetched: %d events, %d unique candidates", len(events), unique_count)
            return events, now
        except Exception as exc:
            if cached_events is not None:
                log.warning("SOCRATES stale cache used (fetch failed: %s, age %.0fs)", exc, age)
                return cached_events, cached_at
            raise RuntimeError(f"SOCRATES unavailable: {exc}") from exc


# ── OMM cache ─────────────────────────────────────────────────────────────────

_OMM_REQUIRED = {"NORAD_CAT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION"}


async def _fetch_one_omm(
    client: httpx.AsyncClient,
    norad_id: str,
    sem: asyncio.Semaphore,
) -> dict:
    """Fetch OMM JSON from CelesTrak GP, build Satrec, return cache entry."""
    async with sem:
        resp = await client.get(
            CELESTRAK_GP_URL,
            params={"CATNR": norad_id, "FORMAT": "json"},
        )
        resp.raise_for_status()

        data = resp.json()
        if not isinstance(data, list) or not data:
            raise ValueError(f"Empty or invalid OMM response for NORAD {norad_id}")

        record = data[0]

        missing = _OMM_REQUIRED - record.keys()
        if missing:
            raise ValueError(f"OMM record missing required fields {missing} for NORAD {norad_id}")

        if str(record.get("NORAD_CAT_ID", "")) != str(norad_id):
            raise ValueError(
                f"NORAD ID mismatch: expected {norad_id}, got {record.get('NORAD_CAT_ID')}"
            )

        satellite = Satrec()
        sgp4_omm.initialize(satellite, record)

        name = record.get("OBJECT_NAME", f"NORAD {norad_id}").strip()
        epoch_str = str(record.get("EPOCH", ""))
        try:
            epoch = datetime.fromisoformat(
                epoch_str.replace("Z", "+00:00")
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            epoch = datetime.now(timezone.utc)

        entry = {
            "satrec": satellite,
            "record": record,
            "name": name,
            "epoch": epoch,
            "retrieved_at": datetime.now(timezone.utc),
        }
        log.info("OMM fetched: NORAD %s (%s, epoch %s)", norad_id, name, _iso(epoch))
        return entry


async def _get_omm_entries(norad_ids: list[str]) -> dict[str, dict]:
    """
    Return OMM cache entries for all requested NORAD IDs.
    Fetches missing/stale entries in parallel (max MAX_CONCURRENT_OMM).
    Returns only the successfully loaded entries (may omit some on error).
    """
    now = datetime.now(timezone.utc)
    need_fetch: list[str] = []

    async with cache_lock:
        for nid in norad_ids:
            entry = omm_cache.get(nid)
            if entry is None:
                need_fetch.append(nid)
                log.info("OMM cache miss: NORAD %s", nid)
            else:
                age = (now - entry["retrieved_at"]).total_seconds()
                if age >= ORBITAL_ELEMENTS_CACHE_TTL_SECONDS:
                    need_fetch.append(nid)
                    log.info("OMM cache miss (stale): NORAD %s (age %.0fs)", nid, age)
                else:
                    log.info("OMM cache hit: NORAD %s (age %.0fs)", nid, age)

    if need_fetch:
        sem = asyncio.Semaphore(MAX_CONCURRENT_OMM)
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(headers=headers, timeout=OMM_TIMEOUT) as client:
            tasks = {nid: _fetch_one_omm(client, nid, sem) for nid in need_fetch}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        async with cache_lock:
            for nid, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    log.warning("OMM unavailable: NORAD %s — %s", nid, result)
                    # keep stale entry if any
                else:
                    omm_cache[nid] = result

    # return snapshot of what we have
    async with cache_lock:
        return {nid: omm_cache[nid] for nid in norad_ids if nid in omm_cache}


# ── SGP4 vectorized computation ───────────────────────────────────────────────

def _build_jd_fr(timestamps: list[datetime]) -> tuple[np.ndarray, np.ndarray]:
    """Convert list of UTC datetimes to parallel jd / fr arrays."""
    jd_arr = np.empty(len(timestamps))
    fr_arr = np.empty(len(timestamps))
    for i, dt in enumerate(timestamps):
        jd_arr[i], fr_arr[i] = jday(
            dt.year, dt.month, dt.day,
            dt.hour, dt.minute,
            dt.second + dt.microsecond * 1e-6,
        )
    return jd_arr, fr_arr


def _propagate_all(
    sat_map: dict[str, Satrec],
    jd_arr: np.ndarray,
    fr_arr: np.ndarray,
) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """
    Run sgp4_array for every Satrec.
    Returns {norad_id: (errors, positions, velocities)}.
    Must be called in a thread (CPU-bound).
    """
    out: dict[str, tuple] = {}
    for nid, sat in sat_map.items():
        errors, positions, velocities = sat.sgp4_array(jd_arr, fr_arr)
        out[nid] = (errors, positions, velocities)
    return out


# ── health endpoint ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── positions endpoint ────────────────────────────────────────────────────────

@app.post("/api/v1/orbits/positions")
async def positions(body: PositionsRequest):
    t_total_start = time.monotonic()

    # ── 1. Parse & validate request ──────────────────────────────────────────
    raw_start = body.start_time
    raw_end = body.end_time

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

    duration_seconds = (end_time - start_time).total_seconds()
    if duration_seconds > 86400:
        return _err(422, "VALIDATION_ERROR",
                    "Requested window exceeds maximum of 24 hours")

    # ── 2. Build time grid ────────────────────────────────────────────────────
    sample_count = math.ceil(duration_seconds / 60)
    timestamps = [start_time + timedelta(seconds=i * 60) for i in range(sample_count)]
    log.info("Time grid: %d samples from %s to %s", sample_count, _iso(start_time), _iso(end_time))

    # ── 3. Get SOCRATES candidates ────────────────────────────────────────────
    global_warnings: list[str] = []
    try:
        events, socrates_retrieved_at = await _get_socrates_events()
    except RuntimeError as exc:
        return _err(503, "SOURCE_UNAVAILABLE", str(exc))

    candidate_ids: list[str] = list(
        dict.fromkeys(
            str(ev["other_norad"]).strip()
            for ev in events
            if ev.get("other_norad") and str(ev["other_norad"]).strip()
        )
    )
    log.info("SOCRATES candidates: %d unique objects", len(candidate_ids))

    all_norad_ids = [ISS_NORAD] + candidate_ids

    # ── 4. Fetch OMM ──────────────────────────────────────────────────────────
    try:
        omm_entries = await _get_omm_entries(all_norad_ids)
    except Exception as exc:
        return _err(500, "ORBIT_SERVICE_ERROR", f"OMM fetch error: {exc}")

    # ISS OMM is mandatory
    if ISS_NORAD not in omm_entries:
        return _err(502, "ORBITAL_ELEMENTS_ERROR",
                    f"Could not retrieve orbital elements for ISS (NORAD {ISS_NORAD})")

    # Track which candidates failed to load
    failed_omm: list[str] = []
    for nid in candidate_ids:
        if nid not in omm_entries:
            failed_omm.append(nid)
            global_warnings.append(
                f"Для NORAD {nid} отсутствуют доступные публичные орбитальные элементы"
            )

    if failed_omm:
        log.warning("OMM unavailable for %d candidates: %s", len(failed_omm), failed_omm)

    # Build satellite map (ISS + available candidates): norad_id → Satrec
    sat_map: dict[str, Satrec] = {
        nid: omm_entries[nid]["satrec"] for nid in all_norad_ids if nid in omm_entries
    }

    # ── 5. Vectorized SGP4 ────────────────────────────────────────────────────
    jd_arr, fr_arr = _build_jd_fr(timestamps)

    t_sgp4 = time.monotonic()
    try:
        prop_results = await asyncio.to_thread(_propagate_all, sat_map, jd_arr, fr_arr)
    except Exception as exc:
        return _err(500, "ORBIT_SERVICE_ERROR", f"SGP4 propagation failed: {exc}")

    log.info("SGP4 calc time: %.3fs for %d objects × %d samples",
             time.monotonic() - t_sgp4, len(sat_map), sample_count)

    # ── 6. Validate ISS propagation ───────────────────────────────────────────
    iss_errors, iss_positions, iss_velocities = prop_results[ISS_NORAD]
    bad_iss = [i for i, e in enumerate(iss_errors) if int(e) != 0]
    if bad_iss:
        return _err(422, "PROPAGATION_ERROR",
                    f"ISS SGP4 propagation error at {len(bad_iss)} time step(s): "
                    f"error codes {set(int(iss_errors[i]) for i in bad_iss)}")

    # ── 7. Validate candidate propagation ────────────────────────────────────
    # Candidate is fully excluded if it has ANY error across the time window
    valid_candidate_ids: list[str] = []
    excluded_candidates: set[str] = set()

    for nid in candidate_ids:
        if nid not in prop_results:
            # OMM was not available — already warned above
            excluded_candidates.add(nid)
            continue
        errors, _, _ = prop_results[nid]
        bad = [i for i, e in enumerate(errors) if int(e) != 0]
        if bad:
            name = omm_entries[nid]["name"] if nid in omm_entries else nid
            global_warnings.append(
                f"NORAD {nid} ({name}): SGP4 error at {len(bad)} step(s) "
                f"(codes {set(int(errors[i]) for i in bad)}) — excluded from screened_objects"
            )
            excluded_candidates.add(nid)
        else:
            valid_candidate_ids.append(nid)

    # ── 8. Build samples ──────────────────────────────────────────────────────
    t_build = time.monotonic()
    samples: list[dict] = []

    for i, ts in enumerate(timestamps):
        iss_pos = iss_positions[i]
        iss_vel = iss_velocities[i]

        screened: list[dict] = []
        for nid in valid_candidate_ids:
            errors, positions, velocities = prop_results[nid]
            pos = positions[i]
            vel = velocities[i]
            entry = omm_entries[nid]
            screened.append({
                "norad_id": int(nid),
                "name": entry["name"],
                "object_type": None,
                "position_km": {
                    "x": round(float(pos[0]), 3),
                    "y": round(float(pos[1]), 3),
                    "z": round(float(pos[2]), 3),
                },
                "velocity_km_s": {
                    "x": round(float(vel[0]), 3),
                    "y": round(float(vel[1]), 3),
                    "z": round(float(vel[2]), 3),
                },
            })

        samples.append({
            "timestamp": _iso(ts),
            "iss": {
                "norad_id": 25544,
                "position_km": {
                    "x": round(float(iss_pos[0]), 3),
                    "y": round(float(iss_pos[1]), 3),
                    "z": round(float(iss_pos[2]), 3),
                },
                "velocity_km_s": {
                    "x": round(float(iss_vel[0]), 3),
                    "y": round(float(iss_vel[1]), 3),
                    "z": round(float(iss_vel[2]), 3),
                },
            },
            "screened_objects": screened,
        })

    log.info("Response build time: %.3fs", time.monotonic() - t_build)

    # ── 9. Data quality ───────────────────────────────────────────────────────
    # elements_epoch: oldest epoch among satellites that actually participated
    participating_epochs: list[datetime] = []
    for nid in [ISS_NORAD] + valid_candidate_ids:
        if nid in omm_entries:
            participating_epochs.append(omm_entries[nid]["epoch"])
    elements_epoch = min(participating_epochs) if participating_epochs else datetime.now(timezone.utc)

    if not candidate_ids:
        dq_status = "NO_CANDIDATES"
    elif excluded_candidates or failed_omm:
        dq_status = "PARTIAL"
    else:
        dq_status = "COMPLETE"

    calculated_at = datetime.now(timezone.utc)

    # ── 10. Compose response ──────────────────────────────────────────────────
    response_body = {
        "request": {
            "start_time": _iso(start_time),
            "end_time": _iso(end_time),
            "step_seconds": 60,
        },
        "coordinate_frame": "TEME",
        "position_unit": "km",
        "velocity_unit": "km/s",
        "source": {
            "name": "CelesTrak SOCRATES Plus / CelesTrak GP",
            "retrieved_at": _iso(socrates_retrieved_at),
        },
        "samples": samples,
        "data_quality": {
            "status": dq_status,
            "warnings": global_warnings,
            "elements_epoch": _iso(elements_epoch),
            "calculated_at": _iso(calculated_at),
        },
    }

    log.info(
        "Total request time: %.3fs | candidates: %d valid / %d total | samples: %d | status: %s",
        time.monotonic() - t_total_start,
        len(valid_candidate_ids),
        len(candidate_ids),
        len(samples),
        dq_status,
    )

    if _HAS_ORJSON:
        return _JSONResponse(content=response_body)
    return JSONResponse(content=response_body)


# ── cache invalidation (для будущего механизма принудительного обновления) ────

async def invalidate_orbit_caches() -> None:
    """Clear SOCRATES and OMM/SGP4 in-memory caches."""
    async with cache_lock:
        socrates_cache["events"] = None
        socrates_cache["retrieved_at"] = None
        omm_cache.clear()
    log.info("Orbit caches invalidated (SOCRATES + OMM)")


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
