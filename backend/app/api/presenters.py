from __future__ import annotations

import numpy as np

from app.schemas.models import (
    DataQuality,
    IssState,
    ObjectIdentity,
    ObjectState,
    Vector3,
)
from app.services.models import CalculationResult


def vector(values: np.ndarray) -> Vector3:
    return Vector3(x=float(values[0]), y=float(values[1]), z=float(values[2]))


def iss_state(result: CalculationResult, index: int) -> IssState:
    return IssState(
        position_km=vector(result.iss.positions_km[index]),
        velocity_km_s=vector(result.iss.velocities_km_s[index]),
    )


def nearest_state(result: CalculationResult, index: int) -> ObjectState | None:
    elements = result.nearest_elements[index]
    if elements is None:
        return None
    return ObjectState(
        norad_id=elements.norad_id,
        name=elements.name,
        object_type=elements.object_type,
        position_km=vector(result.nearest_positions_km[index]),
        velocity_km_s=vector(result.nearest_velocities_km_s[index]),
    )


def nearest_identity(result: CalculationResult, index: int) -> ObjectIdentity | None:
    elements = result.nearest_elements[index]
    if elements is None:
        return None
    return ObjectIdentity(
        norad_id=elements.norad_id,
        name=elements.name,
        object_type=elements.object_type,
    )


def data_quality(result: CalculationResult) -> DataQuality:
    return DataQuality(
        status=result.quality_status,
        warnings=list(result.warnings),
        elements_epoch=result.elements_epoch,
        calculated_at=result.calculated_at,
    )
