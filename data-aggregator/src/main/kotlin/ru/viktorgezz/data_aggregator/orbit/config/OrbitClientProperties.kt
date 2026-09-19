package ru.viktorgezz.data_aggregator.orbit.config

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties(prefix = "orbit.client")
data class OrbitClientProperties(
    val urlBase: String,
    val urlPositions: String,
    val urlDistances: String,
    val urlHealth: String,
    val connectTimeoutSecond: Long = 3,
    val readTimeoutSecond: Long = 10,
    val writeTimeoutSecond: Long = 10,
)
