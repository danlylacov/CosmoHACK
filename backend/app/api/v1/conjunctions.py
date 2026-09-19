from __future__ import annotations

from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends

from app.api.dependencies import get_calculation_service
from app.api.presenters import data_quality, nearest_identity
from app.schemas.models import (
    ConjunctionRequest,
    ConjunctionRequestInfo,
    ConjunctionResponse,
    DistanceSample,
)
from app.services.critical_intervals import CriticalIntervalService
from app.services.orbit_calculation import OrbitCalculationService

router = APIRouter(prefix="/conjunctions", tags=["conjunctions"])


@router.post(
    "/distances",
    response_model=ConjunctionResponse,
    summary="Distances to the nearest screened object",
    description=(
        "The optional critical_distance_km is a proximity-screening threshold, not a "
        "collision probability. Without it, is_critical is null and critical_intervals "
        "is empty. A distance exactly equal to the threshold is not critical."
    ),
)
async def distances(
    request: ConjunctionRequest,
    service: Annotated[OrbitCalculationService, Depends(get_calculation_service)],
) -> ConjunctionResponse:
    result = await service.calculate(request.start_time, request.end_time)
    threshold = request.critical_distance_km
    samples: list[DistanceSample] = []
    for index, timestamp in enumerate(result.times):
        distance = result.distances_km[index]
        relative_speed = result.relative_speeds_km_s[index]
        valid = bool(np.isfinite(distance))
        samples.append(
            DistanceSample(
                timestamp=timestamp,
                nearest_object=nearest_identity(result, index),
                distance_km=float(distance) if valid else None,
                relative_speed_km_s=float(relative_speed) if valid else None,
                is_critical=(float(distance) < threshold)
                if valid and threshold is not None
                else None,
            )
        )

    summary, intervals = CriticalIntervalService().build(result, threshold, request.end_time)
    return ConjunctionResponse(
        request=ConjunctionRequestInfo(
            start_time=request.start_time,
            end_time=request.end_time,
            critical_distance_km=threshold,
        ),
        samples=samples,
        summary=summary,
        critical_intervals=intervals,
        data_quality=data_quality(result),
    )
