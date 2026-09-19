package ru.viktorgezz.data_aggregator.orbit.controller

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
import ru.viktorgezz.data_aggregator.common.UpstreamResult
import ru.viktorgezz.data_aggregator.orbit.client.OrbitApiClient
import ru.viktorgezz.data_aggregator.orbit.dto.ConjunctionRequest
import ru.viktorgezz.data_aggregator.orbit.dto.ConjunctionResponse
import ru.viktorgezz.data_aggregator.orbit.dto.ErrorResponse
import ru.viktorgezz.data_aggregator.orbit.dto.HealthResponse
import ru.viktorgezz.data_aggregator.orbit.dto.PositionsRequest
import ru.viktorgezz.data_aggregator.orbit.dto.PositionsResponse

@RestController
@RequestMapping("/orbit")
class OrbitProxyController(private val client: OrbitApiClient) {

    @Operation(summary = "Проксирует health-check вышестоящего orbit-сервиса")
    @ApiResponses(
        ApiResponse(responseCode = "200", content = [Content(schema = Schema(implementation = HealthResponse::class))]),
        ApiResponse(responseCode = "503", description = "Источник CelesTrak / SOCRATES недоступен", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
    )
    @GetMapping("/health")
    fun health(): ResponseEntity<Any> = client.health().toResponseEntity()

    @Operation(summary = "Проксирует расчёт положений МКС и ближайшего отслеживаемого объекта")
    @ApiResponses(
        ApiResponse(responseCode = "200", content = [Content(schema = Schema(implementation = PositionsResponse::class))]),
        ApiResponse(responseCode = "422", description = "Ошибка валидации, устаревшие орбитальные элементы или ошибка SGP4-пропагации", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
        ApiResponse(responseCode = "502", description = "Не удалось разобрать орбитальные элементы из источника выше по цепочке", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
        ApiResponse(responseCode = "503", description = "Источник CelesTrak / SOCRATES недоступен", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
        ApiResponse(responseCode = "500", description = "Необработанная ошибка вышестоящего сервиса", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
    )
    @PostMapping("/orbits/positions")
    fun positions(@RequestBody request: PositionsRequest): ResponseEntity<Any> =
        client.positions(request).toResponseEntity()

    @Operation(summary = "Проксирует расчёт дистанций до ближайшего отслеживаемого объекта")
    @ApiResponses(
        ApiResponse(responseCode = "200", content = [Content(schema = Schema(implementation = ConjunctionResponse::class))]),
        ApiResponse(responseCode = "422", description = "Ошибка валидации, устаревшие орбитальные элементы или ошибка SGP4-пропагации", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
        ApiResponse(responseCode = "502", description = "Не удалось разобрать орбитальные элементы из источника выше по цепочке", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
        ApiResponse(responseCode = "503", description = "Источник CelesTrak / SOCRATES недоступен", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
        ApiResponse(responseCode = "500", description = "Необработанная ошибка вышестоящего сервиса", content = [Content(schema = Schema(implementation = ErrorResponse::class))]),
    )
    @PostMapping("/conjunctions/distances")
    fun conjunctionDistances(@RequestBody request: ConjunctionRequest): ResponseEntity<Any> =
        client.conjunctionDistances(request).toResponseEntity()

    private fun <T> UpstreamResult<T>.toResponseEntity(): ResponseEntity<Any> = when (this) {
        is UpstreamResult.Success -> ResponseEntity.status(status).body(body)
        is UpstreamResult.Failure -> ResponseEntity.status(status).body(error)
    }
}
