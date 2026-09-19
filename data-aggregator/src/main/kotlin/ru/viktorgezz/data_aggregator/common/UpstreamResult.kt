package ru.viktorgezz.data_aggregator.common

import ru.viktorgezz.data_aggregator.weather.dto.ErrorResponse

sealed interface UpstreamResult<out T> {
    data class Success<T>(val status: Int, val body: T) : UpstreamResult<T>
    data class Failure(val status: Int, val error: ErrorResponse) : UpstreamResult<Nothing>
}