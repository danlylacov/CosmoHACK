package ru.viktorgezz.data_aggregator.weather.config

import okhttp3.OkHttpClient
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import ru.viktorgezz.data_aggregator.weather.client.WeatherApiClient
import tools.jackson.databind.json.JsonMapper
import java.util.concurrent.TimeUnit

@Configuration
@EnableConfigurationProperties(WeatherClientProperties::class)
class WeatherClientConfig {

    @Bean
    fun weatherOkHttpClient(properties: WeatherClientProperties): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(properties.connectTimeoutSecond, TimeUnit.SECONDS)
            .readTimeout(properties.readTimeoutSecond, TimeUnit.SECONDS)
            .writeTimeout(properties.writeTimeoutSecond, TimeUnit.SECONDS)
            .build()

    @Bean
    fun weatherApiClient(
        okHttpClient: OkHttpClient,
        jsonMapper: JsonMapper,
        properties: WeatherClientProperties,
    ): WeatherApiClient = WeatherApiClient(
        okHttpClient = okHttpClient,
        jsonMapper = jsonMapper,
        properties = properties
    )
}
