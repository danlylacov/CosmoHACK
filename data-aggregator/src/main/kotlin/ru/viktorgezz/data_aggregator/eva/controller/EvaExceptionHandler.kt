package ru.viktorgezz.data_aggregator.eva.controller

import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import ru.viktorgezz.data_aggregator.eva.dto.EvaErrorBody
import ru.viktorgezz.data_aggregator.eva.dto.EvaErrorResponse

@RestControllerAdvice(basePackages = ["ru.viktorgezz.data_aggregator.eva"])
class EvaExceptionHandler {

    @ExceptionHandler(EvaUpstreamUnavailableException::class)
    fun handleUnavailable(e: EvaUpstreamUnavailableException): ResponseEntity<EvaErrorResponse> =
        ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(
            EvaErrorResponse(EvaErrorBody(code = "SOURCE_UNAVAILABLE", message = e.message ?: "Сервис недоступен")),
        )
}

class EvaUpstreamUnavailableException(message: String, cause: Throwable? = null) : RuntimeException(message, cause)
