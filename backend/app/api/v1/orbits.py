from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_calculation_service
from app.api.presenters import data_quality, iss_state, nearest_state
from app.schemas.models import (
    PositionSample,
    PositionsRequest,
    PositionsResponse,
    RequestInfo,
    SourceInfo,
)
from app.services.orbit_calculation import OrbitCalculationService

router = APIRouter(prefix="/orbits", tags=["orbits"])


@router.post(
    "/positions",
    response_model=PositionsResponse,
    summary="ISS and nearest screened-object states",
    description=(
        "Returns one TEME sample per second in [start_time, end_time). "
        "The nearest object is selected from current ISS SOCRATES candidates. "
        "It is null when no candidate can be propagated; no coordinates are fabricated."
    ),
)
async def positions(
    request: PositionsRequest,
    service: Annotated[OrbitCalculationService, Depends(get_calculation_service)],
) -> PositionsResponse:
    result = await service.calculate(request.start_time, request.end_time)
    samples = [
        PositionSample(
            timestamp=timestamp,
            iss=iss_state(result, index),
            nearest_object=nearest_state(result, index),
        )
        for index, timestamp in enumerate(result.times)
    ]
    return PositionsResponse(
        request=RequestInfo(start_time=request.start_time, end_time=request.end_time),
        source=SourceInfo(name=result.source, retrieved_at=result.retrieved_at),
        samples=samples,
        data_quality=data_quality(result),
    )
