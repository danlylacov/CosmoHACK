package ru.viktorgezz.data_aggregator.weather.dto

import com.fasterxml.jackson.annotation.JsonProperty

data class ForecastRequest(
    @JsonProperty("origin") val origin: String,
    @JsonProperty("cutoff") val cutoff: String? = null,
    @JsonProperty("data") val data: String? = null,
    @JsonProperty("checkpoint") val checkpoint: String? = null,
    @JsonProperty("policy") val policy: String? = null,
)

data class WeatherValidationError(
    @JsonProperty("loc") val loc: List<Any>,
    @JsonProperty("msg") val msg: String,
    @JsonProperty("type") val type: String,
    @JsonProperty("input") val input: Any? = null,
    @JsonProperty("ctx") val ctx: Map<String, Any?>? = null,
)

data class WeatherHTTPValidationError(
    @JsonProperty("detail") val detail: List<WeatherValidationError> = emptyList(),
)

data class WeatherErrorResponse(
    @JsonProperty("detail") val detail: String,
)
