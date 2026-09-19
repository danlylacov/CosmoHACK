package ru.viktorgezz.data_aggregator.eva.dto

import com.fasterxml.jackson.annotation.JsonProperty
import java.time.OffsetDateTime

data class EvaWindowsRequest(
    @JsonProperty("start_time") val startTime: OffsetDateTime,
    @JsonProperty("end_time") val endTime: OffsetDateTime,
    @JsonProperty("duration_min") val durationMin: Int,
    @JsonProperty("step_min") val stepMin: Int = 30,
    @JsonProperty("top_k") val topK: Int = 5,
    @JsonProperty("critical_distance_km") val criticalDistanceKm: Double,
)

data class EvaRequestInfo(
    @JsonProperty("start_time") val startTime: OffsetDateTime,
    @JsonProperty("end_time") val endTime: OffsetDateTime,
    @JsonProperty("duration_min") val durationMin: Int,
    @JsonProperty("step_min") val stepMin: Int,
    @JsonProperty("top_k") val topK: Int,
    @JsonProperty("critical_distance_km") val criticalDistanceKm: Double,
)

enum class EvaWindowStatus {
    SAFE,
    CAUTION,
    REQUIRES_REVIEW,
    INSUFFICIENT_DATA,
}

data class EvaWindowFactor(
    @JsonProperty("type") val type: String,
    @JsonProperty("critical_overlap_seconds") val criticalOverlapSeconds: Int,
    @JsonProperty("minimum_distance_km") val minimumDistanceKm: Double? = null,
)

data class EvaWindow(
    @JsonProperty("start") val start: OffsetDateTime,
    @JsonProperty("end") val end: OffsetDateTime,
    @JsonProperty("status") val status: EvaWindowStatus,
    @JsonProperty("safety") val safety: Double? = null,
    @JsonProperty("danger") val danger: Double? = null,
    @JsonProperty("peak_danger") val peakDanger: Double? = null,
    @JsonProperty("average_danger") val averageDanger: Double? = null,
    @JsonProperty("critical_overlap_seconds") val criticalOverlapSeconds: Int,
    @JsonProperty("adverse_duration_seconds") val adverseDurationSeconds: Int,
    @JsonProperty("data_coverage") val dataCoverage: Double,
    @JsonProperty("eva_min") val evaMin: Int? = null,
    @JsonProperty("distance_min_km") val distanceMinKm: Double? = null,
    @JsonProperty("blocked") val blocked: Boolean,
    @JsonProperty("factors") val factors: List<EvaWindowFactor> = emptyList(),
    @JsonProperty("reasons") val reasons: List<String> = emptyList(),
)

data class EvaDataQuality(
    @JsonProperty("status") val status: String? = null,
    @JsonProperty("warnings") val warnings: List<String> = emptyList(),
    @JsonProperty("elements_epoch") val elementsEpoch: OffsetDateTime? = null,
    @JsonProperty("calculated_at") val calculatedAt: OffsetDateTime? = null,
)

data class EvaWindowsResponse(
    @JsonProperty("request") val request: EvaRequestInfo,
    @JsonProperty("horizon_minutes") val horizonMinutes: Int,
    @JsonProperty("windows") val windows: List<EvaWindow> = emptyList(),
    @JsonProperty("data_quality") val dataQuality: EvaDataQuality,
)
