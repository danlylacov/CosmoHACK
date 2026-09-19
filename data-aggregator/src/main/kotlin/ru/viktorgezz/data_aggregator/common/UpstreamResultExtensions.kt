package ru.viktorgezz.data_aggregator.common

import org.springframework.http.ResponseEntity

fun <T, E> UpstreamResult<T, E>.toResponseEntity(): ResponseEntity<Any> = when (this) {
    is UpstreamResult.Success -> ResponseEntity.status(status).body(body)
    is UpstreamResult.Failure -> ResponseEntity.status(status).body(error)
}
