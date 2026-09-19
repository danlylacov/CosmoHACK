package ru.viktorgezz.data_aggregator.weather.config

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties(prefix = "weather.client")
data class WeatherClientProperties(
    val urlBase: String,
    val urlSpaceWeather: String,
    val connectTimeoutSecond: Long = 3,
    val readTimeoutSecond: Long = 10,
    val writeTimeoutSecond: Long = 10,
)
