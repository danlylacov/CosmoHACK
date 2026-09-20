"""POST /api/v1/conjunctions/distances calculation service."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from caches import load_orbit_context
from constants import (
    ISS_NORAD,
    LOGGER_NAME,
    SOCRATES_SCREENING_KM,
    STEP_SECONDS,
    TCA_MARGIN_SECONDS,
)
from payload import data_quality_block, satrec_map, sgp4_error_indices
from propagate import build_jd_fr, propagate_all
from responses import ApiError
from timeutil import (
    iso_utc,
    merge_intervals,
    minute_grid,
    parse_tca,
    seconds_in_intervals,
    union_duration_seconds,
)

log = logging.getLogger(LOGGER_NAME)


def expand_window_with_minutes(
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
        errs, _, _ = prop_results[nid]
        if any(int(e) != 0 for e in errs):
            name = omm_entries[nid]["name"] if nid in omm_entries else nid
            extra_warnings.append(
                f"NORAD {nid} ({name}): SGP4 propagation error — excluded"
            )
            excluded_candidates.add(nid)
        else:
            valid_candidate_ids.append(nid)
    return valid_candidate_ids, excluded_candidates, extra_warnings


def build_minute_samples(
    timestamps: list[datetime],
    n_obj: int,
    distances: np.ndarray,
    rel_speeds: np.ndarray,
    valid_candidate_ids: list[str],
    omm_entries: dict[str, dict],
    critical_km: float | None,
) -> list[dict]:
    samples: list[dict] = []
    for t_idx, ts in enumerate(timestamps):
        if n_obj == 0 or np.isinf(distances[:, t_idx]).all():
            samples.append({
                "timestamp": iso_utc(ts),
                "nearest_object": None,
                "distance_km": None,
                "relative_speed_km_s": None,
                "is_critical": None,
            })
            continue

        best_obj = int(np.argmin(distances[:, t_idx]))
        dist_km = round(float(distances[best_obj, t_idx]), 3)
        speed = round(float(rel_speeds[best_obj, t_idx]), 3)
        nid = valid_candidate_ids[best_obj]
        entry = omm_entries[nid]

        is_critical: bool | None = None
        if critical_km is not None:
            is_critical = bool(dist_km < critical_km)

        samples.append({
            "timestamp": iso_utc(ts),
            "nearest_object": {
                "norad_id": int(nid),
                "name": entry["name"],
                "object_type": None,
            },
            "distance_km": dist_km,
            "relative_speed_km_s": speed,
            "is_critical": is_critical,
        })
    return samples


def build_refine_windows(
    events: list[dict],
    valid_candidate_ids: list[str],
    timestamps: list[datetime],
    distances: np.ndarray,
    start_time: datetime,
    end_time: datetime,
    n_obj: int,
    critical_km: float | None,
) -> dict[str, list[tuple[datetime, datetime]]]:
    cand_idx_of: dict[str, int] = {nid: i for i, nid in enumerate(valid_candidate_ids)}
    margin = timedelta(seconds=TCA_MARGIN_SECONDS)
    refine_windows: dict[str, list[tuple[datetime, datetime]]] = {
        nid: [] for nid in valid_candidate_ids
    }

    for ev in events:
        nid = str(ev.get("other_norad", "")).strip()
        if nid not in cand_idx_of:
            continue
        tca = parse_tca(str(ev.get("tca_utc", "")))
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
                expanded.append(expand_window_with_minutes(
                    win_s, win_e, obj_i, timestamps, distances,
                    critical_km, start_time, end_time,
                ))
            refine_windows[nid] = merge_intervals(expanded)
    else:
        for nid in valid_candidate_ids:
            refine_windows[nid] = merge_intervals(refine_windows[nid])
    return refine_windows


def build_critical_intervals(
    valid_candidate_ids: list[str],
    omm_entries: dict[str, dict],
    sec_timestamps: list[datetime],
    sec_distances: np.ndarray,
    sec_speeds: np.ndarray | None,
    critical_km: float,
) -> tuple[list[dict], list[tuple[datetime, datetime]]]:
    critical_intervals: list[dict] = []
    all_critical_second_intervals: list[tuple[datetime, datetime]] = []
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
                    "start_time": iso_utc(ci_start),
                    "end_time": iso_utc(ci_end),
                    "tca": iso_utc(ci_tca_ts),
                    "minimum_distance_km": round(ci_min_dist, 3),
                    "maximum_relative_speed_km_s": round(ci_max_speed, 3),
                    "object": {
                        "norad_id": int(nid),
                        "name": entry["name"],
                        "object_type": None,
                    },
                })
                all_critical_second_intervals.append((ci_start, ci_end))
                in_critical = False

        if in_critical and ci_start is not None:
            ci_end = sec_timestamps[-1]
            critical_intervals.append({
                "start_time": iso_utc(ci_start),
                "end_time": iso_utc(ci_end),
                "tca": iso_utc(ci_tca_ts),
                "minimum_distance_km": round(ci_min_dist, 3),
                "maximum_relative_speed_km_s": round(ci_max_speed, 3),
                "object": {
                    "norad_id": int(nid),
                    "name": entry["name"],
                    "object_type": None,
                },
            })
            all_critical_second_intervals.append((ci_start, ci_end))

    critical_intervals.sort(key=lambda x: x["start_time"])
    return critical_intervals, all_critical_second_intervals


async def compute_distances(
    start_time: datetime,
    end_time: datetime,
    critical_km: float | None,
) -> dict[str, Any]:
    t_total_start = time.monotonic()
    duration_seconds = (end_time - start_time).total_seconds()

    threshold_warnings: list[str] = []
    if critical_km is not None and critical_km > SOCRATES_SCREENING_KM:
        threshold_warnings.append(
            "Порог превышает область исходного скрининга SOCRATES; результат может быть неполным."
        )

    timestamps = minute_grid(start_time, end_time)
    sample_count = len(timestamps)

    try:
        ctx = await load_orbit_context(start_time, end_time)
    except RuntimeError as exc:
        raise ApiError(503, "SOURCE_UNAVAILABLE", str(exc)) from exc

    events = ctx["events"]
    candidate_ids = ctx["candidate_ids"]
    omm_entries = ctx["omm_entries"]
    failed_omm = ctx["failed_omm"]
    global_warnings: list[str] = list(ctx["warnings"]) + threshold_warnings

    log.info("[distances] candidates: %d unique objects", len(candidate_ids))

    if ISS_NORAD not in omm_entries:
        raise ApiError(
            502,
            "ORBITAL_ELEMENTS_ERROR",
            f"Could not retrieve orbital elements for ISS (NORAD {ISS_NORAD})",
        )

    all_norad_ids = [ISS_NORAD] + candidate_ids
    sat_map = satrec_map(omm_entries, all_norad_ids)

    jd_arr, fr_arr = build_jd_fr(timestamps)
    t_prop = time.monotonic()
    try:
        prop_results = await asyncio.to_thread(propagate_all, sat_map, jd_arr, fr_arr)
    except Exception as exc:
        raise ApiError(500, "ORBIT_SERVICE_ERROR", f"SGP4 propagation failed: {exc}") from exc
    log.info("[distances] minute propagation: %.3fs for %d objects × %d samples",
             time.monotonic() - t_prop, len(sat_map), sample_count)

    iss_errors, iss_positions, iss_velocities = prop_results[ISS_NORAD]
    bad_iss = sgp4_error_indices(iss_errors)
    if bad_iss:
        raise ApiError(
            422,
            "PROPAGATION_ERROR",
            f"ISS SGP4 error at {len(bad_iss)} step(s): "
            f"codes {set(int(iss_errors[i]) for i in bad_iss)}",
        )

    valid_candidate_ids, excluded_candidates, extra_warnings = _select_valid_candidates(
        candidate_ids, prop_results, omm_entries,
    )
    global_warnings.extend(extra_warnings)

    n_obj = len(valid_candidate_ids)
    t_count = sample_count

    if n_obj > 0:
        cand_pos = np.empty((n_obj, t_count, 3))
        cand_vel = np.empty((n_obj, t_count, 3))
        for i, nid in enumerate(valid_candidate_ids):
            _, pos, vel = prop_results[nid]
            cand_pos[i] = pos
            cand_vel[i] = vel

        delta_pos = cand_pos - iss_positions[np.newaxis, :, :]
        distances = np.linalg.norm(delta_pos, axis=2)
        delta_vel = cand_vel - iss_velocities[np.newaxis, :, :]
        rel_speeds = np.linalg.norm(delta_vel, axis=2)
    else:
        distances = np.empty((0, t_count))
        rel_speeds = np.empty((0, t_count))

    samples = build_minute_samples(
        timestamps, n_obj, distances, rel_speeds,
        valid_candidate_ids, omm_entries, critical_km,
    )

    refine_windows = build_refine_windows(
        events, valid_candidate_ids, timestamps, distances,
        start_time, end_time, n_obj, critical_km,
    )

    all_windows = [w for wins in refine_windows.values() for w in wins]
    coverage = 0.0
    if duration_seconds > 0 and all_windows:
        coverage = union_duration_seconds(all_windows) / duration_seconds

    use_full_1s = n_obj > 0 and (n_obj <= 3 or coverage > 0.3)
    mode = "full_1s" if use_full_1s else "windows"

    if use_full_1s:
        n_sec = max(int(duration_seconds), 0)
        sec_timestamps = [start_time + timedelta(seconds=i) for i in range(n_sec)]
    else:
        sec_timestamps = seconds_in_intervals(all_windows)

    sec_distances: np.ndarray | None = None
    sec_speeds: np.ndarray | None = None
    t_refine = time.monotonic()

    if n_obj > 0 and sec_timestamps:
        sec_sat_map = {
            ISS_NORAD: omm_entries[ISS_NORAD]["satrec"],
            **{nid: omm_entries[nid]["satrec"] for nid in valid_candidate_ids},
        }
        sec_jd, sec_fr = build_jd_fr(sec_timestamps)
        try:
            sec_prop = await asyncio.to_thread(propagate_all, sec_sat_map, sec_jd, sec_fr)
        except Exception as exc:
            raise ApiError(500, "ORBIT_SERVICE_ERROR", f"SGP4 second-resolution failed: {exc}") from exc

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

    global_min_dist: float | None = None
    global_min_ts: datetime | None = None
    global_min_nid: str | None = None

    def _update_global_min(dist_val: float, ts_val: datetime, nid_val: str) -> None:
        nonlocal global_min_dist, global_min_ts, global_min_nid
        if not math.isfinite(dist_val):
            return
        if global_min_dist is None or dist_val < global_min_dist:
            global_min_dist = dist_val
            global_min_ts = ts_val
            global_min_nid = nid_val

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

    critical_intervals: list[dict] = []
    all_critical_second_intervals: list[tuple[datetime, datetime]] = []

    if critical_km is not None and sec_distances is not None and n_obj > 0:
        critical_intervals, all_critical_second_intervals = build_critical_intervals(
            valid_candidate_ids, omm_entries, sec_timestamps,
            sec_distances, sec_speeds, critical_km,
        )

    critical_duration_seconds = union_duration_seconds(all_critical_second_intervals)

    if global_min_dist is not None and global_min_nid is not None:
        min_nid_entry = omm_entries[global_min_nid]
        summary = {
            "minimum_distance_km": round(global_min_dist, 3),
            "tca": iso_utc(global_min_ts),
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

    dq = data_quality_block(
        candidate_ids, excluded_candidates, failed_omm,
        global_warnings, omm_entries, valid_candidate_ids,
    )

    response_body = {
        "request": {
            "start_time": iso_utc(start_time),
            "end_time": iso_utc(end_time),
            "step_seconds": STEP_SECONDS,
            "critical_distance_km": critical_km,
        },
        "distance_unit": "km",
        "speed_unit": "km/s",
        "samples": samples,
        "summary": summary,
        "critical_intervals": critical_intervals,
        "data_quality": dq,
    }

    log.info(
        "[distances] total: %.3fs | candidates: %d valid/%d | samples: %d | "
        "refinement=%s seconds=%d | critical_intervals: %d | status: %s",
        time.monotonic() - t_total_start,
        len(valid_candidate_ids), len(candidate_ids),
        len(samples),
        mode, len(sec_timestamps),
        len(critical_intervals),
        dq["status"],
    )
    return response_body
