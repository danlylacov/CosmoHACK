package ru.viktorgezz.data_aggregator.orbit.config

import okhttp3.OkHttpClient
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import ru.viktorgezz.data_aggregator.orbit.client.OrbitApiClient
import tools.jackson.databind.json.JsonMapper
import java.util.concurrent.TimeUnit

@Configuration
@EnableConfigurationProperties(OrbitClientProperties::class)
class OrbitClientConfig {

    @Bean
    fun orbitOkHttpClient(properties: OrbitClientProperties): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(properties.connectTimeoutSecond, TimeUnit.SECONDS)
            .readTimeout(properties.readTimeoutSecond, TimeUnit.SECONDS)
            .writeTimeout(properties.writeTimeoutSecond, TimeUnit.SECONDS)
            .build()

    @Bean
    fun orbitApiClient(
        okHttpClient: OkHttpClient,
        jsonMapper: JsonMapper,
        properties: OrbitClientProperties,
    ): OrbitApiClient = OrbitApiClient(
        okHttpClient = okHttpClient,
        jsonMapper = jsonMapper,
        properties = properties
    )
}
