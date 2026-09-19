from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt

from app.providers.base import OrbitalElements

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
IntArray = npt.NDArray[np.int64]


@dataclass(slots=True)
class PropagationResult:
    positions_km: FloatArray
    velocities_km_s: FloatArray
    valid: BoolArray
    error_codes: IntArray


@dataclass(slots=True)
class CalculationResult:
    times: tuple[datetime, ...]
    iss_elements: OrbitalElements
    iss: PropagationResult
    nearest_elements: tuple[OrbitalElements | None, ...]
    nearest_positions_km: FloatArray
    nearest_velocities_km_s: FloatArray
    distances_km: FloatArray
    relative_speeds_km_s: FloatArray
    source: str
    retrieved_at: datetime
    elements_epoch: datetime
    calculated_at: datetime
    quality_status: str
    warnings: tuple[str, ...]
