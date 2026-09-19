package ru.viktorgezz.data_aggregator.eva.controller

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
import ru.viktorgezz.data_aggregator.eva.client.EvaApiClient
import ru.viktorgezz.data_aggregator.eva.dto.EvaErrorResponse
import ru.viktorgezz.data_aggregator.eva.dto.EvaHealthResponse
import ru.viktorgezz.data_aggregator.eva.dto.EvaWindowsRequest
import ru.viktorgezz.data_aggregator.eva.dto.EvaWindowsResponse

@RestController
@RequestMapping("/eva")
class EvaProxyController(private val client: EvaApiClient) {

    @Operation(summary = "Проксирует health-check сервиса ранжирования окон ВКД")
    @ApiResponses(
        ApiResponse(responseCode = "200", content = [Content(schema = Schema(implementation = EvaHealthResponse::class))]),
        ApiResponse(responseCode = "503", description = "Сервис ранжирования окон ВКД недоступен", content = [Content(schema = Schema(implementation = EvaErrorResponse::class))]),
    )
    @GetMapping("/health")
    fun health(): ResponseEntity<Any> = client.health().toResponseEntity()

    @Operation(summary = "Проксирует ранжирование окон ВКД (EVA) заданной длительности по дистанциям из Orbit API")
    @ApiResponses(
        ApiResponse(responseCode = "200", content = [Content(schema = Schema(implementation = EvaWindowsResponse::class))]),
        ApiResponse(responseCode = "422", description = "Ошибка валидации запроса либо горизонт/длительность/шаг окон несовместимы", content = [Content(schema = Schema(implementation = EvaErrorResponse::class))]),
        ApiResponse(responseCode = "502", description = "Не удалось получить корректный ответ от вышестоящего Orbit API", content = [Content(schema = Schema(implementation = EvaErrorResponse::class))]),
        ApiResponse(responseCode = "503", description = "Источник Orbit API недоступен", content = [Content(schema = Schema(implementation = EvaErrorResponse::class))]),
    )
    @PostMapping("/windows")
    fun windows(@RequestBody request: EvaWindowsRequest): ResponseEntity<Any> =
        client.windows(request).toResponseEntity()
}
