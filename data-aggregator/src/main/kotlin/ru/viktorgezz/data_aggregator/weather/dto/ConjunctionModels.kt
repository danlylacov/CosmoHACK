package ru.viktorgezz.data_aggregator.weather.dto

import com.fasterxml.jackson.annotation.JsonProperty
import java.time.OffsetDateTime

data class ConjunctionRequest(
    @JsonProperty("start_time") val startTime: OffsetDateTime,
    @JsonProperty("end_time") val endTime: OffsetDateTime,
    @JsonProperty("critical_distance_km") val criticalDistanceKm: Double? = null,
)

data class ConjunctionRequestInfo(
    @JsonProperty("start_time") val startTime: OffsetDateTime,
    @JsonProperty("end_time") val endTime: OffsetDateTime,
    @JsonProperty("step_seconds") val stepSeconds: Int = 1,
    @JsonProperty("critical_distance_km") val criticalDistanceKm: Double? = null,
)

data class DistanceSample(
    @JsonProperty("timestamp") val timestamp: OffsetDateTime,
    @JsonProperty("nearest_object") val nearestObject: ObjectIdentity? = null,
    @JsonProperty("distance_km") val distanceKm: Double? = null,
    @JsonProperty("relative_speed_km_s") val relativeSpeedKmS: Double? = null,
    @JsonProperty("is_critical") val isCritical: Boolean? = null,
)

data class DistanceSummary(
    @JsonProperty("minimum_distance_km") val minimumDistanceKm: Double? = null,
    @JsonProperty("tca") val tca: OffsetDateTime? = null,
    @JsonProperty("nearest_object") val nearestObject: SummaryObject? = null,
    @JsonProperty("critical_duration_seconds") val criticalDurationSeconds: Int,
)

data class CriticalInterval(
    @JsonProperty("start_time") val startTime: OffsetDateTime,
    @JsonProperty("end_time") val endTime: OffsetDateTime,
    @JsonProperty("tca") val tca: OffsetDateTime,
    @JsonProperty("minimum_distance_km") val minimumDistanceKm: Double,
    @JsonProperty("maximum_relative_speed_km_s") val maximumRelativeSpeedKmS: Double,
    @JsonProperty("object") val objectIdentity: ObjectIdentity,
)

data class ConjunctionResponse(
    @JsonProperty("request") val request: ConjunctionRequestInfo,
    @JsonProperty("distance_unit") val distanceUnit: String = "km",
    @JsonProperty("speed_unit") val speedUnit: String = "km/s",
    @JsonProperty("samples") val samples: List<DistanceSample> = emptyList(),
    @JsonProperty("summary") val summary: DistanceSummary,
    @JsonProperty("critical_intervals") val criticalIntervals: List<CriticalInterval> = emptyList(),
    @JsonProperty("data_quality") val dataQuality: DataQuality,
)
