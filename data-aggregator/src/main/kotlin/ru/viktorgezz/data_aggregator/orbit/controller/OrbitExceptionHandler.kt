package ru.viktorgezz.data_aggregator.orbit.controller

import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import ru.viktorgezz.data_aggregator.orbit.dto.ErrorBody
import ru.viktorgezz.data_aggregator.orbit.dto.ErrorCode
import ru.viktorgezz.data_aggregator.orbit.dto.ErrorResponse

@RestControllerAdvice(basePackages = ["ru.viktorgezz.data_aggregator.orbit"])
class OrbitExceptionHandler {

    @ExceptionHandler(OrbitUpstreamUnavailableException::class)
    fun handleUnavailable(e: OrbitUpstreamUnavailableException): ResponseEntity<ErrorResponse> =
        ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(
            ErrorResponse(ErrorBody(code = ErrorCode.SOURCE_UNAVAILABLE, message = e.message ?: "Сервис недоступен")),
        )
}

class OrbitUpstreamUnavailableException(message: String, cause: Throwable? = null) : RuntimeException(message, cause)