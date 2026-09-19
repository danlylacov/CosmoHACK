package ru.viktorgezz.data_aggregator.weather.controller

import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import ru.viktorgezz.data_aggregator.weather.dto.WeatherErrorResponse

@RestControllerAdvice(basePackages = ["ru.viktorgezz.data_aggregator.weather"])
class WeatherExceptionHandler {

    @ExceptionHandler(WeatherUpstreamUnavailableException::class)
    fun handleUnavailable(e: WeatherUpstreamUnavailableException): ResponseEntity<WeatherErrorResponse> =
        ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(
            WeatherErrorResponse(detail = e.message ?: "Сервис недоступен"),
        )
}

class WeatherUpstreamUnavailableException(message: String, cause: Throwable? = null) : RuntimeException(message, cause)
