from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from app.providers.base import OrbitalElements
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


def utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _vector_dict(values: list[float]) -> dict[str, float]:
    return {"x": float(values[0]), "y": float(values[1]), "z": float(values[2])}


def _identity_dict(elements: OrbitalElements) -> dict[str, object]:
    return {
        "norad_id": elements.norad_id,
        "name": elements.name,
        "object_type": elements.object_type,
    }


def position_sample_dicts(result: CalculationResult) -> list[dict[str, object]]:
    iss_positions = result.iss.positions_km.tolist()
    iss_velocities = result.iss.velocities_km_s.tolist()
    nearest_positions = result.nearest_positions_km.tolist()
    nearest_velocities = result.nearest_velocities_km_s.tolist()
    identities: dict[int, dict[str, object]] = {}
    samples: list[dict[str, object]] = []
    for index, timestamp in enumerate(result.times):
        elements = result.nearest_elements[index]
        nearest = None
        if elements is not None:
            identity = identities.get(id(elements))
            if identity is None:
                identity = _identity_dict(elements)
                identities[id(elements)] = identity
            nearest = {
                **identity,
                "position_km": _vector_dict(nearest_positions[index]),
                "velocity_km_s": _vector_dict(nearest_velocities[index]),
            }
        samples.append(
            {
                "timestamp": utc_z(timestamp),
                "iss": {
                    "norad_id": 25544,
                    "position_km": _vector_dict(iss_positions[index]),
                    "velocity_km_s": _vector_dict(iss_velocities[index]),
                },
                "nearest_object": nearest,
            }
        )
    return samples


def distance_sample_dicts(
    result: CalculationResult,
    threshold_km: float | None,
) -> list[dict[str, object]]:
    distances = result.distances_km.tolist()
    speeds = result.relative_speeds_km_s.tolist()
    identities: dict[int, dict[str, object]] = {}
    samples: list[dict[str, object]] = []
    for index, timestamp in enumerate(result.times):
        distance = distances[index]
        relative_speed = speeds[index]
        valid = bool(np.isfinite(distance))
        elements = result.nearest_elements[index]
        nearest = None
        if elements is not None:
            nearest = identities.get(id(elements))
            if nearest is None:
                nearest = _identity_dict(elements)
                identities[id(elements)] = nearest
        samples.append(
            {
                "timestamp": utc_z(timestamp),
                "nearest_object": nearest,
                "distance_km": float(distance) if valid else None,
                "relative_speed_km_s": float(relative_speed) if valid else None,
                "is_critical": (float(distance) < threshold_km)
                if valid and threshold_km is not None
                else None,
            }
        )
    return samples
