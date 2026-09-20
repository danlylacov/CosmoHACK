"""Request models for the CosmoHACK Orbit API."""

from __future__ import annotations

from pydantic import BaseModel


class PositionsRequest(BaseModel):
    start_time: str
    end_time: str

    model_config = {"json_schema_extra": {
        "example": {
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:03:00Z",
        }
    }}


class ConjunctionRequest(BaseModel):
    start_time: str
    end_time: str
    critical_distance_km: float | None = None

    model_config = {"json_schema_extra": {
        "example": {
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:03:00Z",
            "critical_distance_km": 5.0,
        }
    }}
