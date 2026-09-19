package ru.viktorgezz.data_aggregator.weather.dto

import com.fasterxml.jackson.annotation.JsonProperty
import java.time.OffsetDateTime

data class PositionsRequest(
    @JsonProperty("start_time") val startTime: OffsetDateTime,
    @JsonProperty("end_time") val endTime: OffsetDateTime,
)

data class RequestInfo(
    @JsonProperty("start_time") val startTime: OffsetDateTime,
    @JsonProperty("end_time") val endTime: OffsetDateTime,
    @JsonProperty("step_seconds") val stepSeconds: Int = 1,
)

data class SourceInfo(
    @JsonProperty("name") val name: String,
    @JsonProperty("retrieved_at") val retrievedAt: OffsetDateTime,
)

data class IssState(
    @JsonProperty("norad_id") val noradId: Int = 25544,
    @JsonProperty("position_km") val positionKm: Vector3,
    @JsonProperty("velocity_km_s") val velocityKmS: Vector3,
)

data class ObjectState(
    @JsonProperty("norad_id") val noradId: Int,
    @JsonProperty("name") val name: String,
    @JsonProperty("object_type") val objectType: String? = null,
    @JsonProperty("position_km") val positionKm: Vector3,
    @JsonProperty("velocity_km_s") val velocityKmS: Vector3,
)

data class PositionSample(
    @JsonProperty("timestamp") val timestamp: OffsetDateTime,
    @JsonProperty("iss") val iss: IssState,
    @JsonProperty("nearest_object") val nearestObject: ObjectState? = null,
)

data class PositionsResponse(
    @JsonProperty("request") val request: RequestInfo,
    @JsonProperty("coordinate_frame") val coordinateFrame: String = "TEME",
    @JsonProperty("position_unit") val positionUnit: String = "km",
    @JsonProperty("velocity_unit") val velocityUnit: String = "km/s",
    @JsonProperty("source") val source: SourceInfo,
    @JsonProperty("samples") val samples: List<PositionSample> = emptyList(),
    @JsonProperty("data_quality") val dataQuality: DataQuality,
)
