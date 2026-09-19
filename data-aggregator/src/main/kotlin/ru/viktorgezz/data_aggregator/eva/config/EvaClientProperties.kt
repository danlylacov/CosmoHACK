package ru.viktorgezz.data_aggregator.eva.config

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties(prefix = "eva.client")
data class EvaClientProperties(
    val urlBase: String,
    val urlWindows: String,
    val urlHealth: String,
    val connectTimeoutSecond: Long = 3,
    val readTimeoutSecond: Long = 10,
    val writeTimeoutSecond: Long = 10,
)
