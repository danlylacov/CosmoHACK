package ru.viktorgezz.data_aggregator.weather.controller

import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import io.swagger.v3.oas.annotations.responses.ApiResponse
import io.swagger.v3.oas.annotations.responses.ApiResponses
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController
import ru.viktorgezz.data_aggregator.common.toResponseEntity
import ru.viktorgezz.data_aggregator.weather.client.WeatherApiClient
import ru.viktorgezz.data_aggregator.weather.dto.ForecastRequest
import ru.viktorgezz.data_aggregator.weather.dto.WeatherErrorResponse

@RestController
@RequestMapping("/weather")
class WeatherProxyController(private val client: WeatherApiClient) {

    @Operation(summary = "Проксирует health-check сервиса SEPNET-SGR")
    @ApiResponses(
        ApiResponse(responseCode = "200", description = "Успешный ответ вышестоящего сервиса"),
        ApiResponse(responseCode = "503", description = "Сервис SEPNET-SGR недоступен", content = [Content(schema = Schema(implementation = WeatherErrorResponse::class))]),
    )
    @GetMapping("/health")
    fun health(): ResponseEntity<Any> = client.health().toResponseEntity()

    @Operation(summary = "Проксирует расчёт прогноза SEPNET-SGR (forecast.json)")
    @ApiResponses(
        ApiResponse(responseCode = "200", description = "Успешный ответ вышестоящего сервиса (forecast.json)"),
        ApiResponse(responseCode = "422", description = "Ошибка валидации запроса", content = [Content(schema = Schema(implementation = WeatherErrorResponse::class))]),
        ApiResponse(responseCode = "503", description = "Сервис SEPNET-SGR недоступен", content = [Content(schema = Schema(implementation = WeatherErrorResponse::class))]),
    )
    @PostMapping("/forecast")
    fun forecast(@RequestBody request: ForecastRequest): ResponseEntity<Any> =
        client.forecast(request).toResponseEntity()
}
