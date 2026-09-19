from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import get_calculation_service
from app.api.presenters import data_quality, position_sample_dicts, utc_z
from app.schemas.models import PositionsRequest, PositionsResponse
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
    # #region agent log
    _t0 = __import__("time").monotonic()
    # #endregion
    result = await service.calculate(request.start_time, request.end_time)
    # #region agent log
    _t1 = __import__("time").monotonic()
    # #endregion
    samples = position_sample_dicts(result)
    payload = {
        "request": {
            "start_time": utc_z(request.start_time),
            "end_time": utc_z(request.end_time),
            "step_seconds": 1,
        },
        "coordinate_frame": "TEME",
        "position_unit": "km",
        "velocity_unit": "km/s",
        "source": {"name": result.source, "retrieved_at": utc_z(result.retrieved_at)},
        "samples": samples,
        "data_quality": data_quality(result).model_dump(mode="json"),
    }
    # #region agent log
    try:
        import json as _json, time as _time
        with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
            _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"C","location":"orbits.py:positions","message":"positions handler built samples","data":{"n_samples":len(samples),"calc_s":round(_t1-_t0,3),"samples_s":round(__import__("time").monotonic()-_t1,3)},"timestamp":int(_time.time()*1000)})+"\n")
    except Exception:
        pass
    # #endregion
    return JSONResponse(payload)
