package ru.viktorgezz.data_aggregator.health

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

@RestController
class HealthController {

    @GetMapping("/health")
    fun health(): HealthResponse = HealthResponse(status = "UP")
}

data class HealthResponse(val status: String)
