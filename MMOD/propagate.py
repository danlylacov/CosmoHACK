"""Vectorized SGP4 helpers. CPU-bound work is intended for asyncio.to_thread."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from sgp4.api import Satrec, jday


def build_jd_fr(timestamps: list[datetime]) -> tuple[np.ndarray, np.ndarray]:
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


def propagate_all(
    sat_map: dict[str, Satrec],
    jd_arr: np.ndarray,
    fr_arr: np.ndarray,
) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """
    Run sgp4_array for every Satrec.
    Returns {norad_id: (errors, positions, velocities)}.
    Must be called in a thread (CPU-bound).
    """
    out: dict[str, tuple[Any, Any, Any]] = {}
    for nid, sat in sat_map.items():
        errors, positions, velocities = sat.sgp4_array(jd_arr, fr_arr)
        out[nid] = (errors, positions, velocities)
    return out
