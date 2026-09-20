"""In-memory caches and upstream IO for SOCRATES, OMM, and SATCAT."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sgp4 import omm as sgp4_omm
from sgp4.api import Satrec

from constants import (
    CELESTRAK_GP_URL,
    CELESTRAK_SATCAT_URL,
    ISS_NORAD,
    LOGGER_NAME,
    MAX_CONCURRENT_OMM,
    OMM_REQUIRED,
    OMM_TIMEOUT,
    ORBITAL_ELEMENTS_CACHE_TTL_SECONDS,
    SATCAT_CACHE_TTL_SECONDS,
    SOCRATES_CACHE_TTL_SECONDS,
    TCA_MARGIN_SECONDS,
    USER_AGENT,
)
from get_data import fetch_iss_conjunctions
from satcat import classify_norad
from timeutil import iso_utc, parse_tca

log = logging.getLogger(LOGGER_NAME)

# socrates_cache: {"events": list | None, "retrieved_at": datetime | None}
socrates_cache: dict[str, Any] = {"events": None, "retrieved_at": None}

# omm_cache: {norad_id_str: {"satrec": Satrec, "record": dict, "name": str,
#                             "epoch": datetime, "retrieved_at": datetime}}
omm_cache: dict[str, dict] = {}

# satcat_cache: {norad_id_str: {"record": dict | None, "retrieved_at": datetime}}
satcat_cache: dict[str, dict] = {}

cache_lock = asyncio.Lock()


async def get_socrates_events() -> tuple[list[dict], datetime]:
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
            events = await asyncio.to_thread(fetch_iss_conjunctions, "TCA", 1000)
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


async def fetch_one_omm(
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

        missing = OMM_REQUIRED - record.keys()
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
        log.info("OMM fetched: NORAD %s (%s, epoch %s)", norad_id, name, iso_utc(epoch))
        return entry


async def get_omm_entries(norad_ids: list[str]) -> dict[str, dict]:
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
            tasks = {nid: fetch_one_omm(client, nid, sem) for nid in need_fetch}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        async with cache_lock:
            for nid, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    log.warning("OMM unavailable: NORAD %s — %s", nid, result)
                    # keep stale entry if any
                else:
                    omm_cache[nid] = result

    async with cache_lock:
        return {nid: omm_cache[nid] for nid in norad_ids if nid in omm_cache}


async def fetch_one_satcat(
    client: httpx.AsyncClient,
    norad_id: str,
    sem: asyncio.Semaphore,
) -> dict | None:
    async with sem:
        resp = await client.get(
            CELESTRAK_SATCAT_URL,
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


async def get_satcat_records(norad_ids: list[str]) -> dict[str, dict | None]:
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
            tasks = {nid: fetch_one_satcat(client, nid, sem) for nid in need_fetch}
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


def filter_events_to_window(
    all_events: list[dict],
    start_time: datetime,
    end_time: datetime,
) -> list[dict]:
    margin = timedelta(seconds=TCA_MARGIN_SECONDS)
    window_start = start_time - margin
    window_end = end_time + margin

    window_events: list[dict] = []
    for ev in all_events:
        tca = parse_tca(str(ev.get("tca_utc", "")))
        if tca is None:
            window_events.append(ev)
            continue
        if window_start <= tca <= window_end:
            window_events.append(ev)
    return window_events


def unique_norads(events: list[dict]) -> list[str]:
    return list(
        dict.fromkeys(
            str(ev["other_norad"]).strip()
            for ev in events
            if ev.get("other_norad") and str(ev["other_norad"]).strip()
        )
    )


async def load_orbit_context(start_time: datetime, end_time: datetime) -> dict:
    """
    Shared between both endpoints.
    Fetches (or returns cached) SOCRATES events, filters them to the request
    window, classifies ISS-associated objects, then loads OMM for remaining
    external candidates.
    """
    all_events, socrates_retrieved_at = await get_socrates_events()
    window_events = filter_events_to_window(all_events, start_time, end_time)
    unique_in_window = unique_norads(window_events)

    satcat_records = await get_satcat_records(unique_in_window) if unique_in_window else {}
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
        kind = classify_norad(nid, record, failed)
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
    omm_entries = await get_omm_entries(all_norad_ids)

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


async def invalidate_orbit_caches() -> None:
    """Clear SOCRATES, OMM/SGP4 and SATCAT in-memory caches."""
    async with cache_lock:
        socrates_cache["events"] = None
        socrates_cache["retrieved_at"] = None
        omm_cache.clear()
        satcat_cache.clear()
    log.info("Orbit caches invalidated (SOCRATES + OMM + SATCAT)")
