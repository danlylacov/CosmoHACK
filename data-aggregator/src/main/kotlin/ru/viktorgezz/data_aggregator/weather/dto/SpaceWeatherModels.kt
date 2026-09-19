package ru.viktorgezz.data_aggregator.weather.dto

import com.fasterxml.jackson.annotation.JsonProperty
import java.time.OffsetDateTime

data class SpaceWeatherResponse(
    @JsonProperty("start") val start: OffsetDateTime,
    @JsonProperty("end") val end: OffsetDateTime,
    @JsonProperty("interval_minutes") val intervalMinutes: Int,
    @JsonProperty("eva_coefficient") val evaCoefficient: Double? = null,
    @JsonProperty("records") val records: List<SpaceWeatherRecord> = emptyList(),
)

data class SpaceWeatherRecord(
    @JsonProperty("time") val time: OffsetDateTime,
    @JsonProperty("eva_coefficient") val evaCoefficient: Double? = null,
    @JsonProperty("parameters") val parameters: Map<String, Any?> = emptyMap(),
)

data class WeatherErrorResponse(
    @JsonProperty("detail") val detail: String,
)
