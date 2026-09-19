#!/usr/bin/env python3
"""
CosmoHACK Orbit API — FastAPI app.

Endpoints:
  GET  /health
  POST /api/v1/orbits/positions
  POST /api/v1/conjunctions/distances
"""

from __future__ import annotations

import asyncio
import json
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
_ROOT = _DIR.parent
for _p in (_DIR, _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

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
CELESTRAK_HOSTS = ("celestrak.org", "celestrak.com")
CELESTRAK_GP_PATH = "/NORAD/elements/gp.php"
CELESTRAK_SATCAT_PATH = "/satcat/records.php"
SOCRATES_DUMP_PATH = _DIR / "iss_conjunctions.json"
USER_AGENT = "Mozilla/5.0 (compatible; CosmoHACK-OrbitAPI/1.0)"
SOCRATES_CACHE_TTL_SECONDS = 2 * 60 * 60
ORBITAL_ELEMENTS_CACHE_TTL_SECONDS = 2 * 60 * 60
SATCAT_CACHE_TTL_SECONDS = 2 * 60 * 60
OMM_TIMEOUT = 30.0
MAX_CONCURRENT_OMM = 5
TCA_MARGIN_SECONDS = 120
SOCRATES_SCREENING_KM = 5.0
# Verified catalog IDs of ISS-complex vehicles (not name-based heuristics).
ISS_ASSOCIATED_NORAD_IDS: set[str] = {"100057"}
_ISS_CENTER_MARKERS = {
    "25544", "ISS", "ZARYA", "ISS (ZARYA)", "ISS-ZARYA",
    "1998-067A", "INTERNATIONAL SPACE STATION",
}

# ── module-level cache ────────────────────────────────────────────────────────
# socrates_cache: {"events": list | None, "retrieved_at": datetime | None}
socrates_cache: dict[str, Any] = {"events": None, "retrieved_at": None}

# omm_cache: {norad_id_str: {"satrec": Satrec, "record": dict, "name": str,
#                             "epoch": datetime, "retrieved_at": datetime}}
omm_cache: dict[str, dict] = {}

# satcat_cache: {norad_id_str: {"record": dict | None, "retrieved_at": datetime}}
satcat_cache: dict[str, dict] = {}

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


class ConjunctionRequest(BaseModel):
    start_time: str
    end_time: str
    critical_distance_km: float | None = None

    model_config = {"json_schema_extra": {
        "example": {
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:03:00Z",
            "critical_distance_km": 5.0,
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


def _ok(payload: dict) -> JSONResponse:
    if _HAS_ORJSON:
        return _JSONResponse(content=payload)
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


# ── helpers: time formatting ──────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    """Return ISO-8601 UTC string with Z suffix."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_tca(tca_str: str) -> datetime | None:
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


# ── CelesTrak HTTP ────────────────────────────────────────────────────────────

async def _celestrak_get(
    client: httpx.AsyncClient,
    path: str,
    params: dict,
) -> httpx.Response:
    last_exc: Exception | None = None
    for host in CELESTRAK_HOSTS:
        url = f"https://{host}{path}"
        try:
            return await client.get(url, params=params)
        except httpx.TransportError as exc:
            last_exc = exc
            log.warning("CelesTrak transport error %s: %s", url, exc)
    assert last_exc is not None
    raise last_exc


# ── SOCRATES cache ────────────────────────────────────────────────────────────

def _read_socrates_dump() -> tuple[list[dict], datetime] | None:
    try:
        raw = json.loads(SOCRATES_DUMP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("SOCRATES dump unreadable: %s", exc)
        return None
    events = raw.get("events")
    if not isinstance(events, list):
        return None
    fetched_at = _parse_tca(str(raw.get("fetched_at") or ""))
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)
    return events, fetched_at


def _write_socrates_dump(events: list[dict], retrieved_at: datetime) -> None:
    payload = {
        "fetched_at": retrieved_at.isoformat(),
        "source": "CelesTrak SOCRATES Plus",
        "url": "https://celestrak.org/SOCRATES/table-socrates.php?CATNR=25544",
        "count": len(events),
        "events": events,
    }
    try:
        SOCRATES_DUMP_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("SOCRATES dump not saved: %s", exc)


async def _get_socrates_events() -> tuple[list[dict], datetime]:
    """
    Return (events, retrieved_at).
    Uses in-memory cache with SOCRATES_CACHE_TTL_SECONDS TTL.
    Falls back to stale cache, then local dump, on error.
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
            events = await asyncio.to_thread(fetch_iss_conjunctions, "TCA", 1000)
            socrates_cache["events"] = events
            socrates_cache["retrieved_at"] = now
            unique_count = len(dict.fromkeys(
                e["other_norad"] for e in events if e.get("other_norad")
            ))
            log.info("SOCRATES fetched: %d events, %d unique candidates", len(events), unique_count)
            _write_socrates_dump(events, now)
            return events, now
        except Exception as exc:
            if cached_events is not None:
                log.warning("SOCRATES stale cache used (fetch failed: %s, age %.0fs)", exc, age)
                return cached_events, cached_at
            dump = _read_socrates_dump()
            if dump is not None:
                events, retrieved_at = dump
                socrates_cache["events"] = events
                socrates_cache["retrieved_at"] = retrieved_at
                dump_age = (now - retrieved_at).total_seconds()
                log.warning(
                    "SOCRATES dump used (fetch failed: %s, dump age %.0fs, %d events)",
                    exc, dump_age, len(events),
                )
                return events, retrieved_at
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
        resp = await _celestrak_get(
            client,
            CELESTRAK_GP_PATH,
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


# ── SATCAT classification ─────────────────────────────────────────────────────

def _satcat_field(record: dict, *names: str) -> str:
    lower = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        value = record.get(name)
        if value is None:
            value = lower.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _is_iss_associated_record(record: dict) -> bool:
    orbit_type = _satcat_field(record, "ORBIT_TYPE", "Orbit Type").upper()
    orbit_center = _satcat_field(record, "ORBIT_CENTER", "Orbit Center").upper()
    ops_status = _satcat_field(
        record, "OPS_STATUS_CODE", "OPS_STATUS", "Operational Status"
    ).upper()
    data_status = _satcat_field(record, "DATA_STATUS_CODE", "Data Status Code").upper()

    if orbit_type in {"DOC", "DOCKED", "D"} or "DOCK" in orbit_type:
        return True
    if "DOCK" in ops_status or "DOCK" in data_status:
        return True
    center_compact = orbit_center.replace("_", " ")
    for marker in _ISS_CENTER_MARKERS:
        if marker == orbit_center or marker in center_compact:
            return True
    if orbit_center in ISS_ASSOCIATED_NORAD_IDS or orbit_center == ISS_NORAD:
        return True
    return False


async def _fetch_one_satcat(
    client: httpx.AsyncClient,
    norad_id: str,
    sem: asyncio.Semaphore,
) -> dict | None:
    async with sem:
        resp = await _celestrak_get(
            client,
            CELESTRAK_SATCAT_PATH,
            params={"CATNR": norad_id, "FORMAT": "json"},
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception as exc:
            raise ValueError(f"SATCAT response is not JSON for NORAD {norad_id}") from exc
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            if not data:
                return None
            for key in ("records", "data", "satcat"):
                inner = data.get(key)
                if isinstance(inner, list):
                    return inner[0] if inner else None
            return data
        return None


async def _get_satcat_records(norad_ids: list[str]) -> dict[str, dict | None]:
    now = datetime.now(timezone.utc)
    need_fetch: list[str] = []

    async with cache_lock:
        for nid in norad_ids:
            entry = satcat_cache.get(nid)
            if entry is None:
                need_fetch.append(nid)
                log.info("SATCAT cache miss: NORAD %s", nid)
            else:
                age = (now - entry["retrieved_at"]).total_seconds()
                if age >= SATCAT_CACHE_TTL_SECONDS:
                    need_fetch.append(nid)
                    log.info("SATCAT cache miss (stale): NORAD %s", nid)
                else:
                    log.info("SATCAT cache hit: NORAD %s", nid)

    if need_fetch:
        sem = asyncio.Semaphore(MAX_CONCURRENT_OMM)
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(headers=headers, timeout=OMM_TIMEOUT) as client:
            tasks = {nid: _fetch_one_satcat(client, nid, sem) for nid in need_fetch}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        async with cache_lock:
            for nid, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    log.warning("SATCAT unavailable: NORAD %s — %s", nid, result)
                    if nid not in satcat_cache:
                        satcat_cache[nid] = {
                            "record": None,
                            "retrieved_at": now,
                            "failed": True,
                        }
                else:
                    satcat_cache[nid] = {
                        "record": result,
                        "retrieved_at": datetime.now(timezone.utc),
                        "failed": False,
                    }

    async with cache_lock:
        return {nid: satcat_cache[nid]["record"] for nid in norad_ids if nid in satcat_cache}


def _classify_norad(norad_id: str, satcat_record: dict | None, _satcat_failed: bool) -> str:
    if satcat_record:
        if _is_iss_associated_record(satcat_record):
            return "ISS_ASSOCIATED"
        return "EXTERNAL_CONJUNCTION"
    if norad_id in ISS_ASSOCIATED_NORAD_IDS:
        return "ISS_ASSOCIATED"
    return "UNKNOWN"


# ── shared orbit context ──────────────────────────────────────────────────────

async def _load_orbit_context(start_time: datetime, end_time: datetime) -> dict:
    """
    Shared between both endpoints.
    Fetches (or returns cached) SOCRATES events, filters them to the request
    window, classifies ISS-associated objects, then loads OMM for remaining
    external candidates.
    """
    all_events, socrates_retrieved_at = await _get_socrates_events()
    margin = timedelta(seconds=TCA_MARGIN_SECONDS)
    window_start = start_time - margin
    window_end = end_time + margin

    window_events: list[dict] = []
    for ev in all_events:
        tca = _parse_tca(str(ev.get("tca_utc", "")))
        if tca is None:
            window_events.append(ev)
            continue
        if window_start <= tca <= window_end:
            window_events.append(ev)

    unique_in_window: list[str] = list(
        dict.fromkeys(
            str(ev["other_norad"]).strip()
            for ev in window_events
            if ev.get("other_norad") and str(ev["other_norad"]).strip()
        )
    )

    satcat_records = await _get_satcat_records(unique_in_window) if unique_in_window else {}
    classifications: dict[str, str] = {}
    warnings: list[str] = []
    associated_ids: list[str] = []
    candidate_ids: list[str] = []

    async with cache_lock:
        satcat_failed_ids = {
            nid for nid in unique_in_window
            if satcat_cache.get(nid, {}).get("failed")
        }

    for nid in unique_in_window:
        record = satcat_records.get(nid)
        failed = nid in satcat_failed_ids and record is None
        kind = _classify_norad(nid, record, failed)
        classifications[nid] = kind
        if kind == "ISS_ASSOCIATED":
            associated_ids.append(nid)
            warnings.append(
                f"NORAD {nid} исключён как объект комплекса МКС (ISS_ASSOCIATED)"
            )
        else:
            candidate_ids.append(nid)
            if kind == "UNKNOWN":
                warnings.append(
                    f"NORAD {nid}: статус SATCAT не подтверждён, объект включён как внешний (требуется проверка)"
                )

    log.info(
        "SOCRATES: fetched=%d in_window=%d unique_external=%d associated=%d",
        len(all_events), len(window_events), len(candidate_ids), len(associated_ids),
    )

    all_norad_ids = [ISS_NORAD] + candidate_ids
    omm_entries = await _get_omm_entries(all_norad_ids)

    failed_omm: list[str] = []
    for nid in candidate_ids:
        if nid not in omm_entries:
            failed_omm.append(nid)
            warnings.append(
                f"Для NORAD {nid} отсутствуют доступные публичные орбитальные элементы"
            )

    return {
        "events": window_events,
        "candidate_ids": candidate_ids,
        "omm_entries": omm_entries,
        "failed_omm": failed_omm,
        "warnings": warnings,
        "socrates_retrieved_at": socrates_retrieved_at,
        "classifications": classifications,
        "associated_ids": associated_ids,
    }


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

    # ── 3. Load SOCRATES + OMM (shared cache) ────────────────────────────────
    try:
        ctx = await _load_orbit_context(start_time, end_time)
    except RuntimeError as exc:
        return _err(503, "SOURCE_UNAVAILABLE", str(exc))

    events = ctx["events"]
    candidate_ids = ctx["candidate_ids"]
    omm_entries = ctx["omm_entries"]
    failed_omm = ctx["failed_omm"]
    global_warnings: list[str] = list(ctx["warnings"])
    socrates_retrieved_at = ctx["socrates_retrieved_at"]

    log.info("SOCRATES candidates: %d unique objects", len(candidate_ids))

    # ISS OMM is mandatory
    if ISS_NORAD not in omm_entries:
        return _err(502, "ORBITAL_ELEMENTS_ERROR",
                    f"Could not retrieve orbital elements for ISS (NORAD {ISS_NORAD})")

    if failed_omm:
        log.warning("OMM unavailable for %d candidates: %s", len(failed_omm), failed_omm)

    # Build satellite map (ISS + available candidates): norad_id → Satrec
    all_norad_ids = [ISS_NORAD] + candidate_ids
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


# ── TCA refinement helpers ────────────────────────────────────────────────────

# ── TCA refinement helpers ────────────────────────────────────────────────────

def _seconds_in_intervals(
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


def _expand_window_with_minutes(
    win_s: datetime,
    win_e: datetime,
    obj_idx: int,
    minute_ts: list[datetime],
    distances: np.ndarray,
    critical_km: float,
    start_time: datetime,
    end_time: datetime,
) -> tuple[datetime, datetime]:
    """Widen a TCA window using already-computed minute distances (no extra SGP4)."""
    if distances.shape[0] == 0 or not minute_ts:
        return win_s, win_e

    def dist_at(idx: int) -> float:
        return float(distances[obj_idx, idx])

    idx = 0
    while idx < len(minute_ts) and minute_ts[idx] < win_s:
        idx += 1
    left = max(idx - 1, 0)
    while win_s > start_time and dist_at(left) < critical_km:
        win_s = max(start_time, minute_ts[left])
        if left == 0:
            win_s = start_time
            break
        left -= 1

    right = len(minute_ts) - 1
    while right >= 0 and minute_ts[right] > win_e:
        right -= 1
    right = max(right, 0)
    while win_e < end_time and dist_at(right) < critical_km:
        win_e = min(end_time, minute_ts[right] + timedelta(seconds=60))
        if right >= len(minute_ts) - 1:
            win_e = end_time
            break
        right += 1
    return win_s, win_e


def _merge_intervals(
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


def _union_duration_seconds(
    intervals: list[tuple[datetime, datetime]],
) -> int:
    """Total seconds covered by the union of intervals (no double-counting)."""
    merged = _merge_intervals(intervals)
    return sum(int((e - s).total_seconds()) for s, e in merged)


# ── distances computation (shared by /distances and /eva/windows) ─────────────

async def _compute_distances(
    start_time: datetime,
    end_time: datetime,
    critical_km: float | None,
) -> dict | JSONResponse:
    t_total_start = time.monotonic()
    duration_seconds = (end_time - start_time).total_seconds()

    if critical_km is not None and critical_km <= 0:
        return _err(422, "VALIDATION_ERROR", "critical_distance_km must be > 0")

    threshold_warnings: list[str] = []
    if critical_km is not None and critical_km > SOCRATES_SCREENING_KM:
        threshold_warnings.append(
            "Порог превышает область исходного скрининга SOCRATES; результат может быть неполным."
        )

    # ── 2. Minute time grid ───────────────────────────────────────────────────
    sample_count = math.ceil(duration_seconds / 60)
    timestamps = [start_time + timedelta(seconds=i * 60) for i in range(sample_count)]

    # ── 3. Load SOCRATES + OMM (shared cache) ─────────────────────────────────
    try:
        ctx = await _load_orbit_context(start_time, end_time)
    except RuntimeError as exc:
        return _err(503, "SOURCE_UNAVAILABLE", str(exc))

    events              = ctx["events"]
    candidate_ids       = ctx["candidate_ids"]
    omm_entries         = ctx["omm_entries"]
    failed_omm          = ctx["failed_omm"]
    global_warnings: list[str] = list(ctx["warnings"]) + threshold_warnings
    socrates_retrieved_at = ctx["socrates_retrieved_at"]

    log.info("[distances] candidates: %d unique objects", len(candidate_ids))

    if ISS_NORAD not in omm_entries:
        return _err(502, "ORBITAL_ELEMENTS_ERROR",
                    f"Could not retrieve orbital elements for ISS (NORAD {ISS_NORAD})")

    all_norad_ids = [ISS_NORAD] + candidate_ids
    sat_map: dict[str, Satrec] = {
        nid: omm_entries[nid]["satrec"] for nid in all_norad_ids if nid in omm_entries
    }

    # ── 4. Vectorized minute propagation ─────────────────────────────────────
    jd_arr, fr_arr = _build_jd_fr(timestamps)
    t_prop = time.monotonic()
    try:
        prop_results = await asyncio.to_thread(_propagate_all, sat_map, jd_arr, fr_arr)
    except Exception as exc:
        return _err(500, "ORBIT_SERVICE_ERROR", f"SGP4 propagation failed: {exc}")
    log.info("[distances] minute propagation: %.3fs for %d objects × %d samples",
             time.monotonic() - t_prop, len(sat_map), sample_count)

    # ── 5. Validate ISS propagation ───────────────────────────────────────────
    iss_errors, iss_positions, iss_velocities = prop_results[ISS_NORAD]
    bad_iss = [i for i, e in enumerate(iss_errors) if int(e) != 0]
    if bad_iss:
        return _err(422, "PROPAGATION_ERROR",
                    f"ISS SGP4 error at {len(bad_iss)} step(s): "
                    f"codes {set(int(iss_errors[i]) for i in bad_iss)}")

    # ── 6. Build candidate arrays + distance matrix ───────────────────────────
    valid_candidate_ids: list[str] = []
    excluded_candidates: set[str] = set()

    for nid in candidate_ids:
        if nid not in prop_results:
            excluded_candidates.add(nid)
            continue
        errs, _, _ = prop_results[nid]
        if any(int(e) != 0 for e in errs):
            name = omm_entries[nid]["name"] if nid in omm_entries else nid
            global_warnings.append(
                f"NORAD {nid} ({name}): SGP4 propagation error — excluded"
            )
            excluded_candidates.add(nid)
        else:
            valid_candidate_ids.append(nid)

    n_obj = len(valid_candidate_ids)
    T     = sample_count

    if n_obj > 0:
        cand_pos = np.empty((n_obj, T, 3))
        cand_vel = np.empty((n_obj, T, 3))
        for i, nid in enumerate(valid_candidate_ids):
            _, pos, vel = prop_results[nid]
            cand_pos[i] = pos
            cand_vel[i] = vel

        delta_pos  = cand_pos - iss_positions[np.newaxis, :, :]   # [obj, T, 3]
        distances  = np.linalg.norm(delta_pos, axis=2)             # [obj, T]
        delta_vel  = cand_vel - iss_velocities[np.newaxis, :, :]
        rel_speeds = np.linalg.norm(delta_vel, axis=2)             # [obj, T]
    else:
        distances  = np.empty((0, T))
        rel_speeds = np.empty((0, T))

    # ── 7. Minute samples (nearest per minute) ────────────────────────────────
    samples: list[dict] = []
    for t_idx, ts in enumerate(timestamps):
        if n_obj == 0 or np.isinf(distances[:, t_idx]).all():
            samples.append({
                "timestamp": _iso(ts),
                "nearest_object": None,
                "distance_km": None,
                "relative_speed_km_s": None,
                "is_critical": None,
            })
            continue

        best_obj = int(np.argmin(distances[:, t_idx]))
        dist_km  = round(float(distances[best_obj, t_idx]), 3)
        speed    = round(float(rel_speeds[best_obj, t_idx]), 3)
        nid      = valid_candidate_ids[best_obj]
        entry    = omm_entries[nid]

        is_critical: bool | None = None
        if critical_km is not None:
            is_critical = bool(dist_km < critical_km)

        samples.append({
            "timestamp": _iso(ts),
            "nearest_object": {
                "norad_id": int(nid),
                "name": entry["name"],
                "object_type": None,
            },
            "distance_km": dist_km,
            "relative_speed_km_s": speed,
            "is_critical": is_critical,
        })

    # ── 8. TCA refinement (shared 1-second grid) ──────────────────────────────
    cand_idx_of: dict[str, int] = {nid: i for i, nid in enumerate(valid_candidate_ids)}
    margin = timedelta(seconds=TCA_MARGIN_SECONDS)
    refine_windows: dict[str, list[tuple[datetime, datetime]]] = {
        nid: [] for nid in valid_candidate_ids
    }

    for ev in events:
        nid = str(ev.get("other_norad", "")).strip()
        if nid not in cand_idx_of:
            continue
        tca = _parse_tca(str(ev.get("tca_utc", "")))
        if tca is None:
            obj_i = cand_idx_of[nid]
            best_t = int(np.argmin(distances[obj_i]))
            tca = timestamps[best_t]
        if tca < start_time - margin or tca > end_time + margin:
            continue
        win_s = max(start_time, tca - margin)
        win_e = min(end_time, tca + margin)
        if win_e > win_s:
            refine_windows[nid].append((win_s, win_e))

    if n_obj > 0:
        for nid in valid_candidate_ids:
            if not refine_windows[nid]:
                obj_i = cand_idx_of[nid]
                best_t = int(np.argmin(distances[obj_i]))
                tca = timestamps[best_t]
                refine_windows[nid].append((
                    max(start_time, tca - margin),
                    min(end_time, tca + margin),
                ))

    if critical_km is not None and n_obj > 0:
        for nid in valid_candidate_ids:
            obj_i = cand_idx_of[nid]
            expanded: list[tuple[datetime, datetime]] = []
            for win_s, win_e in refine_windows[nid]:
                expanded.append(_expand_window_with_minutes(
                    win_s, win_e, obj_i, timestamps, distances,
                    critical_km, start_time, end_time,
                ))
            refine_windows[nid] = _merge_intervals(expanded)
    else:
        for nid in valid_candidate_ids:
            refine_windows[nid] = _merge_intervals(refine_windows[nid])

    all_windows = [w for wins in refine_windows.values() for w in wins]
    coverage = 0.0
    if duration_seconds > 0 and all_windows:
        coverage = _union_duration_seconds(all_windows) / duration_seconds

    use_full_1s = n_obj > 0 and (n_obj <= 3 or coverage > 0.3)
    mode = "full_1s" if use_full_1s else "windows"

    if use_full_1s:
        n_sec = max(int(duration_seconds), 0)
        sec_timestamps = [start_time + timedelta(seconds=i) for i in range(n_sec)]
    else:
        sec_timestamps = _seconds_in_intervals(all_windows)

    sec_distances: np.ndarray | None = None
    sec_speeds: np.ndarray | None = None
    t_refine = time.monotonic()

    if n_obj > 0 and sec_timestamps:
        sec_sat_map = {
            ISS_NORAD: omm_entries[ISS_NORAD]["satrec"],
            **{nid: omm_entries[nid]["satrec"] for nid in valid_candidate_ids},
        }
        sec_jd, sec_fr = _build_jd_fr(sec_timestamps)
        try:
            sec_prop = await asyncio.to_thread(_propagate_all, sec_sat_map, sec_jd, sec_fr)
        except Exception as exc:
            return _err(500, "ORBIT_SERVICE_ERROR", f"SGP4 second-resolution failed: {exc}")

        iss_err_s, iss_pos_s, iss_vel_s = sec_prop[ISS_NORAD]
        n_s = len(sec_timestamps)
        cand_pos_s = np.empty((n_obj, n_s, 3))
        cand_vel_s = np.empty((n_obj, n_s, 3))
        valid_sec = np.ones((n_obj, n_s), dtype=bool)
        iss_ok = np.array([int(e) == 0 for e in iss_err_s], dtype=bool)
        for i, nid in enumerate(valid_candidate_ids):
            errs, pos, vel = sec_prop[nid]
            cand_pos_s[i] = pos
            cand_vel_s[i] = vel
            valid_sec[i] = iss_ok & np.array([int(e) == 0 for e in errs], dtype=bool)

        delta_pos_s = cand_pos_s - iss_pos_s[np.newaxis, :, :]
        sec_distances = np.linalg.norm(delta_pos_s, axis=2)
        delta_vel_s = cand_vel_s - iss_vel_s[np.newaxis, :, :]
        sec_speeds = np.linalg.norm(delta_vel_s, axis=2)
        sec_distances = np.where(valid_sec, sec_distances, np.inf)
        sec_speeds = np.where(valid_sec, sec_speeds, np.nan)

    log.info(
        "[distances] refinement: %s, seconds=%d, objects=%d, time=%.3fs, coverage=%.2f",
        mode, len(sec_timestamps), n_obj, time.monotonic() - t_refine, coverage,
    )

    # ── 9. Global minimum for summary ─────────────────────────────────────────
    global_min_dist: float | None = None
    global_min_ts:   datetime | None = None
    global_min_nid:  str | None = None

    def _update_global_min(dist_val: float, ts_val: datetime, nid_val: str) -> None:
        nonlocal global_min_dist, global_min_ts, global_min_nid
        if not math.isfinite(dist_val):
            return
        if global_min_dist is None or dist_val < global_min_dist:
            global_min_dist = dist_val
            global_min_ts   = ts_val
            global_min_nid  = nid_val

    if n_obj > 0:
        for t_idx, ts in enumerate(timestamps):
            col = distances[:, t_idx]
            if not np.isinf(col).all():
                best_obj = int(np.argmin(col))
                _update_global_min(float(col[best_obj]), ts, valid_candidate_ids[best_obj])

    if sec_distances is not None:
        for i, nid in enumerate(valid_candidate_ids):
            row = sec_distances[i]
            if np.isinf(row).all():
                continue
            best_i = int(np.argmin(row))
            _update_global_min(float(row[best_i]), sec_timestamps[best_i], nid)

    # ── 10. Critical intervals + critical_duration ────────────────────────────
    critical_intervals: list[dict] = []
    all_critical_second_intervals: list[tuple[datetime, datetime]] = []

    if critical_km is not None and sec_distances is not None and n_obj > 0:
        n_s = len(sec_timestamps)
        for i, nid in enumerate(valid_candidate_ids):
            entry = omm_entries[nid]
            in_critical = False
            ci_start: datetime | None = None
            ci_min_dist = float("inf")
            ci_max_speed = 0.0
            ci_tca_ts: datetime | None = None

            for k in range(n_s):
                d = float(sec_distances[i, k])
                s = float(sec_speeds[i, k]) if sec_speeds is not None else float("nan")
                is_crit = math.isfinite(d) and d < critical_km

                if is_crit and not in_critical:
                    in_critical = True
                    ci_start = sec_timestamps[k]
                    ci_min_dist = d
                    ci_max_speed = s if math.isfinite(s) else 0.0
                    ci_tca_ts = sec_timestamps[k]
                elif is_crit and in_critical:
                    if d < ci_min_dist:
                        ci_min_dist = d
                        ci_tca_ts = sec_timestamps[k]
                    if math.isfinite(s) and s > ci_max_speed:
                        ci_max_speed = s
                elif not is_crit and in_critical:
                    ci_end = sec_timestamps[k]
                    critical_intervals.append({
                        "start_time": _iso(ci_start),
                        "end_time":   _iso(ci_end),
                        "tca":        _iso(ci_tca_ts),
                        "minimum_distance_km":        round(ci_min_dist, 3),
                        "maximum_relative_speed_km_s": round(ci_max_speed, 3),
                        "object": {
                            "norad_id":   int(nid),
                            "name":       entry["name"],
                            "object_type": None,
                        },
                    })
                    all_critical_second_intervals.append((ci_start, ci_end))
                    in_critical = False

            if in_critical and ci_start is not None:
                ci_end = sec_timestamps[-1]
                critical_intervals.append({
                    "start_time": _iso(ci_start),
                    "end_time":   _iso(ci_end),
                    "tca":        _iso(ci_tca_ts),
                    "minimum_distance_km":        round(ci_min_dist, 3),
                    "maximum_relative_speed_km_s": round(ci_max_speed, 3),
                    "object": {
                        "norad_id":   int(nid),
                        "name":       entry["name"],
                        "object_type": None,
                    },
                })
                all_critical_second_intervals.append((ci_start, ci_end))

        critical_intervals.sort(key=lambda x: x["start_time"])

    critical_duration_seconds = _union_duration_seconds(all_critical_second_intervals)

    # ── 11. Summary ───────────────────────────────────────────────────────────
    if global_min_dist is not None and global_min_nid is not None:
        min_nid_entry = omm_entries[global_min_nid]
        summary = {
            "minimum_distance_km": round(global_min_dist, 3),
            "tca": _iso(global_min_ts),
            "nearest_object": {
                "norad_id": int(global_min_nid),
                "name": min_nid_entry["name"],
            },
            "critical_duration_seconds": critical_duration_seconds,
        }
    else:
        summary = {
            "minimum_distance_km": None,
            "tca": None,
            "nearest_object": None,
            "critical_duration_seconds": critical_duration_seconds,
        }

    # ── 12. Data quality ──────────────────────────────────────────────────────
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

    # ── 13. Compose response ──────────────────────────────────────────────────
    response_body = {
        "request": {
            "start_time": _iso(start_time),
            "end_time":   _iso(end_time),
            "step_seconds": 60,
            "critical_distance_km": critical_km,
        },
        "distance_unit": "km",
        "speed_unit": "km/s",
        "samples": samples,
        "summary": summary,
        "critical_intervals": critical_intervals,
        "data_quality": {
            "status": dq_status,
            "warnings": global_warnings,
            "elements_epoch": _iso(elements_epoch),
            "calculated_at": _iso(calculated_at),
        },
    }

    log.info(
        "[distances] total: %.3fs | candidates: %d valid/%d | samples: %d | "
        "refinement=%s seconds=%d | critical_intervals: %d | status: %s",
        time.monotonic() - t_total_start,
        len(valid_candidate_ids), len(candidate_ids),
        len(samples),
        mode, len(sec_timestamps),
        len(critical_intervals),
        dq_status,
    )

    return response_body


@app.post("/api/v1/conjunctions/distances")
async def distances(body: ConjunctionRequest):
    parsed = _parse_time_window(body.start_time, body.end_time)
    if isinstance(parsed, JSONResponse):
        return parsed
    start_time, end_time = parsed
    result = await _compute_distances(start_time, end_time, body.critical_distance_km)
    if isinstance(result, JSONResponse):
        return result
    return _ok(result)


# ── cache invalidation (для будущего механизма принудительного обновления) ────

async def invalidate_orbit_caches() -> None:
    """Clear SOCRATES, OMM/SGP4 and SATCAT in-memory caches."""
    async with cache_lock:
        socrates_cache["events"] = None
        socrates_cache["retrieved_at"] = None
        omm_cache.clear()
        satcat_cache.clear()
    log.info("Orbit caches invalidated (SOCRATES + OMM + SATCAT)")


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
