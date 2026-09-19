package ru.viktorgezz.data_aggregator.common

sealed interface UpstreamResult<out T, out E> {
    data class Success<T>(val status: Int, val body: T) : UpstreamResult<T, Nothing>
    data class Failure<E>(val status: Int, val error: E) : UpstreamResult<Nothing, E>
}
