package ru.viktorgezz.data_aggregator.weather.dto

import com.fasterxml.jackson.annotation.JsonProperty
import java.time.OffsetDateTime

data class Vector3(
    @JsonProperty("x") val x: Double,
    @JsonProperty("y") val y: Double,
    @JsonProperty("z") val z: Double,
)

data class ObjectIdentity(
    @JsonProperty("norad_id") val noradId: Int,
    @JsonProperty("name") val name: String,
    @JsonProperty("object_type") val objectType: String? = null,
)

data class SummaryObject(
    @JsonProperty("norad_id") val noradId: Int,
    @JsonProperty("name") val name: String,
)

enum class DataQualityStatus {
    COMPLETE,
    PARTIAL,
    NO_CANDIDATES,
}

data class DataQuality(
    @JsonProperty("status") val status: DataQualityStatus,
    @JsonProperty("warnings") val warnings: List<String> = emptyList(),
    @JsonProperty("elements_epoch") val elementsEpoch: OffsetDateTime,
    @JsonProperty("calculated_at") val calculatedAt: OffsetDateTime,
)

enum class ErrorCode {
    VALIDATION_ERROR,
    STALE_ORBITAL_ELEMENTS,
    PROPAGATION_ERROR,
    ORBITAL_ELEMENTS_ERROR,
    SOURCE_UNAVAILABLE,
    ORBIT_SERVICE_ERROR,
}

data class ErrorBody(
    @JsonProperty("code") val code: ErrorCode,
    @JsonProperty("message") val message: String,
    @JsonProperty("details") val details: Map<String, Any?>? = null,
)

data class ErrorResponse(
    @JsonProperty("error") val error: ErrorBody,
)

data class HealthResponse(
    @JsonProperty("status") val status: String = "ok",
)
