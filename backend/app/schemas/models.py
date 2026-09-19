from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeRangeRequest(ApiModel):
    start_time: datetime = Field(examples=["2026-09-19T00:00:00Z"])
    end_time: datetime = Field(examples=["2026-09-19T00:01:00Z"])

    @field_validator("start_time", "end_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_range(self) -> TimeRangeRequest:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be later than start_time")
        if self.end_time - self.start_time > timedelta(hours=24):
            raise ValueError("requested period must not exceed 24 hours")
        return self


class PositionsRequest(TimeRangeRequest):
    pass


class ConjunctionRequest(TimeRangeRequest):
    critical_distance_km: float | None = Field(default=None, gt=0)


class Vector3(ApiModel):
    x: float
    y: float
    z: float


class ObjectIdentity(ApiModel):
    norad_id: int
    name: str
    object_type: str | None = None


class ObjectState(ObjectIdentity):
    position_km: Vector3
    velocity_km_s: Vector3


class IssState(ApiModel):
    norad_id: int = 25544
    position_km: Vector3
    velocity_km_s: Vector3


class SourceInfo(ApiModel):
    name: str
    retrieved_at: datetime


class RequestInfo(ApiModel):
    start_time: datetime
    end_time: datetime
    step_seconds: Literal[1] = 1


class ConjunctionRequestInfo(RequestInfo):
    critical_distance_km: float | None = None


class DataQuality(ApiModel):
    status: Literal["COMPLETE", "PARTIAL", "NO_CANDIDATES"]
    warnings: list[str] = Field(default_factory=list)
    elements_epoch: datetime
    calculated_at: datetime


class PositionSample(ApiModel):
    timestamp: datetime
    iss: IssState
    nearest_object: ObjectState | None


class PositionsResponse(ApiModel):
    request: RequestInfo
    coordinate_frame: Literal["TEME"] = "TEME"
    position_unit: Literal["km"] = "km"
    velocity_unit: Literal["km/s"] = "km/s"
    source: SourceInfo
    samples: list[PositionSample]
    data_quality: DataQuality


class DistanceSample(ApiModel):
    timestamp: datetime
    nearest_object: ObjectIdentity | None
    distance_km: float | None
    relative_speed_km_s: float | None
    is_critical: bool | None = None


class SummaryObject(ApiModel):
    norad_id: int
    name: str


class DistanceSummary(ApiModel):
    minimum_distance_km: float | None
    tca: datetime | None
    nearest_object: SummaryObject | None
    critical_duration_seconds: int


class CriticalInterval(ApiModel):
    start_time: datetime
    end_time: datetime
    tca: datetime
    minimum_distance_km: float
    maximum_relative_speed_km_s: float
    object: ObjectIdentity


class ConjunctionResponse(ApiModel):
    request: ConjunctionRequestInfo
    distance_unit: Literal["km"] = "km"
    speed_unit: Literal["km/s"] = "km/s"
    samples: list[DistanceSample]
    summary: DistanceSummary
    critical_intervals: list[CriticalInterval]
    data_quality: DataQuality
