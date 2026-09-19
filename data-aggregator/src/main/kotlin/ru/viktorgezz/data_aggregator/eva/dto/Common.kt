package ru.viktorgezz.data_aggregator.eva.dto

import com.fasterxml.jackson.annotation.JsonProperty

data class EvaErrorBody(
    @JsonProperty("code") val code: String,
    @JsonProperty("message") val message: String,
    @JsonProperty("details") val details: Map<String, Any?>? = null,
)

data class EvaErrorResponse(
    @JsonProperty("error") val error: EvaErrorBody,
)

data class EvaValidationError(
    @JsonProperty("loc") val loc: List<Any>,
    @JsonProperty("msg") val msg: String,
    @JsonProperty("type") val type: String,
    @JsonProperty("input") val input: Any? = null,
    @JsonProperty("ctx") val ctx: Map<String, Any?>? = null,
)

data class EvaHTTPValidationError(
    @JsonProperty("detail") val detail: List<EvaValidationError> = emptyList(),
)

data class EvaHealthResponse(
    @JsonProperty("status") val status: String = "ok",
    @JsonProperty("service") val service: String? = null,
)
