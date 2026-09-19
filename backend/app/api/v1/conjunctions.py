from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_calculation_service
from app.api.presenters import data_quality, distance_sample_dicts, utc_z
from app.schemas.models import ConjunctionRequest, ConjunctionResponse
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
    # #region agent log
    _t0 = __import__("time").monotonic()
    # #endregion
    result = await service.calculate(request.start_time, request.end_time)
    # #region agent log
    _t1 = __import__("time").monotonic()
    # #endregion
    threshold = request.critical_distance_km
    samples = distance_sample_dicts(result, threshold)
    summary, intervals = CriticalIntervalService().build(result, threshold, request.end_time)
    payload = {
        "request": {
            "start_time": utc_z(request.start_time),
            "end_time": utc_z(request.end_time),
            "step_seconds": 1,
            "critical_distance_km": threshold,
        },
        "distance_unit": "km",
        "speed_unit": "km/s",
        "samples": samples,
        "summary": summary.model_dump(mode="json"),
        "critical_intervals": [interval.model_dump(mode="json") for interval in intervals],
        "data_quality": data_quality(result).model_dump(mode="json"),
    }
    # #region agent log
    try:
        import json as _json, time as _time
        with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
            _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"C","location":"conjunctions.py:distances","message":"distances handler built samples","data":{"n_samples":len(samples),"n_intervals":len(intervals),"calc_s":round(_t1-_t0,3),"samples_s":round(__import__("time").monotonic()-_t1,3)},"timestamp":int(_time.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion
    return JSONResponse(payload)
