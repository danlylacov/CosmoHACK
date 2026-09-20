"""Shared response-fragment builders. Field names and rounding match the original API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from constants import ISS_NORAD
from timeutil import iso_utc


def vec3(vec: Any) -> dict[str, float]:
    return {
        "x": round(float(vec[0]), 3),
        "y": round(float(vec[1]), 3),
        "z": round(float(vec[2]), 3),
    }


def data_quality_status(
    candidate_ids: list[str],
    excluded_candidates: set[str],
    failed_omm: list[str],
) -> str:
    if not candidate_ids:
        return "NO_CANDIDATES"
    if excluded_candidates or failed_omm:
        return "PARTIAL"
    return "COMPLETE"


def oldest_elements_epoch(omm_entries: dict[str, dict], norad_ids: list[str]) -> datetime:
    participating_epochs: list[datetime] = []
    for nid in norad_ids:
        if nid in omm_entries:
            participating_epochs.append(omm_entries[nid]["epoch"])
    return min(participating_epochs) if participating_epochs else datetime.now(timezone.utc)


def satrec_map(omm_entries: dict[str, dict], norad_ids: list[str]) -> dict:
    return {nid: omm_entries[nid]["satrec"] for nid in norad_ids if nid in omm_entries}


def sgp4_error_indices(errors: Any) -> list[int]:
    return [i for i, e in enumerate(errors) if int(e) != 0]


def data_quality_block(
    candidate_ids: list[str],
    excluded_candidates: set[str],
    failed_omm: list[str],
    warnings: list[str],
    omm_entries: dict[str, dict],
    valid_candidate_ids: list[str],
) -> dict[str, Any]:
    elements_epoch = oldest_elements_epoch(omm_entries, [ISS_NORAD] + valid_candidate_ids)
    calculated_at = datetime.now(timezone.utc)
    return {
        "status": data_quality_status(candidate_ids, excluded_candidates, failed_omm),
        "warnings": warnings,
        "elements_epoch": iso_utc(elements_epoch),
        "calculated_at": iso_utc(calculated_at),
    }
