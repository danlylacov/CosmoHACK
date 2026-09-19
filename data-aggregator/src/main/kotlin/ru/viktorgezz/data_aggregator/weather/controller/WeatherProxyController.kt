package ru.viktorgezz.data_aggregator.weather.controller

import io.swagger.v3.oas.annotations.Operation
import io.swagger.v3.oas.annotations.media.Content
import io.swagger.v3.oas.annotations.media.Schema
import io.swagger.v3.oas.annotations.responses.ApiResponse
import io.swagger.v3.oas.annotations.responses.ApiResponses
import org.springframework.format.annotation.DateTimeFormat
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import ru.viktorgezz.data_aggregator.common.toResponseEntity
import ru.viktorgezz.data_aggregator.weather.client.WeatherApiClient
import ru.viktorgezz.data_aggregator.weather.dto.SpaceWeatherResponse
import ru.viktorgezz.data_aggregator.weather.dto.WeatherErrorResponse
import java.time.OffsetDateTime

@RestController
@RequestMapping("/weather")
class WeatherProxyController(private val client: WeatherApiClient) {

    @Operation(summary = "Проксирует космическую погоду и коэффициент EVA на интервале")
    @ApiResponses(
        ApiResponse(responseCode = "200", content = [Content(schema = Schema(implementation = SpaceWeatherResponse::class))]),
        ApiResponse(responseCode = "400", description = "Некорректный интервал (start >= end)", content = [Content(schema = Schema(implementation = WeatherErrorResponse::class))]),
        ApiResponse(responseCode = "404", description = "CSV с данными не найден", content = [Content(schema = Schema(implementation = WeatherErrorResponse::class))]),
        ApiResponse(responseCode = "503", description = "Источник данных космической погоды недоступен", content = [Content(schema = Schema(implementation = WeatherErrorResponse::class))]),
    )
    @GetMapping("/space-weather")
    fun spaceWeather(
        @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) start: OffsetDateTime,
        @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) end: OffsetDateTime,
    ): ResponseEntity<Any> = client.spaceWeather(start, end).toResponseEntity()
}
