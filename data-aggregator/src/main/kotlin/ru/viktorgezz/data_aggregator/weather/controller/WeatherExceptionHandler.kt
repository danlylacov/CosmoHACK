package ru.viktorgezz.data_aggregator.weather.controller

import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import ru.viktorgezz.data_aggregator.weather.client.WeatherUpstreamUnavailableException
import ru.viktorgezz.data_aggregator.weather.dto.ErrorBody
import ru.viktorgezz.data_aggregator.weather.dto.ErrorCode
import ru.viktorgezz.data_aggregator.weather.dto.ErrorResponse

@RestControllerAdvice(basePackages = ["ru.viktorgezz.data_aggregator.weather"])
class WeatherExceptionHandler {

    @ExceptionHandler(WeatherUpstreamUnavailableException::class)
    fun handleUnavailable(e: WeatherUpstreamUnavailableException): ResponseEntity<ErrorResponse> =
        ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(
            ErrorResponse(ErrorBody(code = ErrorCode.SOURCE_UNAVAILABLE, message = e.message ?: "Сервис недоступен")),
        )
}
