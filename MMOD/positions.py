"""POST /api/v1/orbits/positions calculation service."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from caches import load_orbit_context
from constants import ISS_NORAD, LOGGER_NAME, STEP_SECONDS
from payload import data_quality_block, satrec_map, sgp4_error_indices, vec3
from propagate import build_jd_fr, propagate_all
from responses import ApiError
from timeutil import iso_utc, minute_grid

log = logging.getLogger(LOGGER_NAME)


def _select_valid_candidates(
    candidate_ids: list[str],
    prop_results: dict[str, tuple],
    omm_entries: dict[str, dict],
) -> tuple[list[str], set[str], list[str]]:
    valid_candidate_ids: list[str] = []
    excluded_candidates: set[str] = set()
    extra_warnings: list[str] = []

    for nid in candidate_ids:
        if nid not in prop_results:
            excluded_candidates.add(nid)
            continue
        errors, _, _ = prop_results[nid]
        bad = sgp4_error_indices(errors)
        if bad:
            name = omm_entries[nid]["name"] if nid in omm_entries else nid
            extra_warnings.append(
                f"NORAD {nid} ({name}): SGP4 error at {len(bad)} step(s) "
                f"(codes {set(int(errors[i]) for i in bad)}) — excluded from screened_objects"
            )
            excluded_candidates.add(nid)
        else:
            valid_candidate_ids.append(nid)
    return valid_candidate_ids, excluded_candidates, extra_warnings


def build_position_samples(
    timestamps: list[datetime],
    iss_positions: Any,
    iss_velocities: Any,
    valid_candidate_ids: list[str],
    prop_results: dict[str, tuple],
    omm_entries: dict[str, dict],
) -> list[dict]:
    samples: list[dict] = []
    for i, ts in enumerate(timestamps):
        iss_pos = iss_positions[i]
        iss_vel = iss_velocities[i]

        screened: list[dict] = []
        for nid in valid_candidate_ids:
            _errors, positions, velocities = prop_results[nid]
            pos = positions[i]
            vel = velocities[i]
            entry = omm_entries[nid]
            screened.append({
                "norad_id": int(nid),
                "name": entry["name"],
                "object_type": None,
                "position_km": vec3(pos),
                "velocity_km_s": vec3(vel),
            })

        samples.append({
            "timestamp": iso_utc(ts),
            "iss": {
                "norad_id": 25544,
                "position_km": vec3(iss_pos),
                "velocity_km_s": vec3(iss_vel),
            },
            "screened_objects": screened,
        })
    return samples


async def compute_positions(start_time: datetime, end_time: datetime) -> dict[str, Any]:
    t_total_start = time.monotonic()

    timestamps = minute_grid(start_time, end_time)
    sample_count = len(timestamps)
    log.info("Time grid: %d samples from %s to %s", sample_count, iso_utc(start_time), iso_utc(end_time))

    try:
        ctx = await load_orbit_context(start_time, end_time)
    except RuntimeError as exc:
        raise ApiError(503, "SOURCE_UNAVAILABLE", str(exc)) from exc

    candidate_ids = ctx["candidate_ids"]
    omm_entries = ctx["omm_entries"]
    failed_omm = ctx["failed_omm"]
    global_warnings: list[str] = list(ctx["warnings"])
    socrates_retrieved_at = ctx["socrates_retrieved_at"]

    log.info("SOCRATES candidates: %d unique objects", len(candidate_ids))

    if ISS_NORAD not in omm_entries:
        raise ApiError(
            502,
            "ORBITAL_ELEMENTS_ERROR",
            f"Could not retrieve orbital elements for ISS (NORAD {ISS_NORAD})",
        )

    if failed_omm:
        log.warning("OMM unavailable for %d candidates: %s", len(failed_omm), failed_omm)

    all_norad_ids = [ISS_NORAD] + candidate_ids
    sat_map = satrec_map(omm_entries, all_norad_ids)

    jd_arr, fr_arr = build_jd_fr(timestamps)

    t_sgp4 = time.monotonic()
    try:
        prop_results = await asyncio.to_thread(propagate_all, sat_map, jd_arr, fr_arr)
    except Exception as exc:
        raise ApiError(500, "ORBIT_SERVICE_ERROR", f"SGP4 propagation failed: {exc}") from exc

    log.info("SGP4 calc time: %.3fs for %d objects × %d samples",
             time.monotonic() - t_sgp4, len(sat_map), sample_count)

    iss_errors, iss_positions, iss_velocities = prop_results[ISS_NORAD]
    bad_iss = sgp4_error_indices(iss_errors)
    if bad_iss:
        raise ApiError(
            422,
            "PROPAGATION_ERROR",
            f"ISS SGP4 propagation error at {len(bad_iss)} time step(s): "
            f"error codes {set(int(iss_errors[i]) for i in bad_iss)}",
        )

    valid_candidate_ids, excluded_candidates, extra_warnings = _select_valid_candidates(
        candidate_ids, prop_results, omm_entries,
    )
    global_warnings.extend(extra_warnings)

    t_build = time.monotonic()
    samples = build_position_samples(
        timestamps, iss_positions, iss_velocities,
        valid_candidate_ids, prop_results, omm_entries,
    )
    log.info("Response build time: %.3fs", time.monotonic() - t_build)

    dq = data_quality_block(
        candidate_ids, excluded_candidates, failed_omm,
        global_warnings, omm_entries, valid_candidate_ids,
    )

    response_body = {
        "request": {
            "start_time": iso_utc(start_time),
            "end_time": iso_utc(end_time),
            "step_seconds": STEP_SECONDS,
        },
        "coordinate_frame": "TEME",
        "position_unit": "km",
        "velocity_unit": "km/s",
        "source": {
            "name": "CelesTrak SOCRATES Plus / CelesTrak GP",
            "retrieved_at": iso_utc(socrates_retrieved_at),
        },
        "samples": samples,
        "data_quality": dq,
    }

    log.info(
        "Total request time: %.3fs | candidates: %d valid / %d total | samples: %d | status: %s",
        time.monotonic() - t_total_start,
        len(valid_candidate_ids),
        len(candidate_ids),
        len(samples),
        dq["status"],
    )
    return response_body
