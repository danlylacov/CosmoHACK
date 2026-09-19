package ru.viktorgezz.data_aggregator.eva.config

import okhttp3.OkHttpClient
import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import ru.viktorgezz.data_aggregator.eva.client.EvaApiClient
import tools.jackson.databind.json.JsonMapper
import java.util.concurrent.TimeUnit

@Configuration
@EnableConfigurationProperties(EvaClientProperties::class)
class EvaClientConfig {

    @Bean
    fun evaOkHttpClient(properties: EvaClientProperties): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(properties.connectTimeoutSecond, TimeUnit.SECONDS)
            .readTimeout(properties.readTimeoutSecond, TimeUnit.SECONDS)
            .writeTimeout(properties.writeTimeoutSecond, TimeUnit.SECONDS)
            .build()

    @Bean
    fun evaApiClient(
        jsonMapper: JsonMapper,
        properties: EvaClientProperties,
    ): EvaApiClient = EvaApiClient(
        okHttpClient = evaOkHttpClient(properties),
        jsonMapper = jsonMapper,
        properties = properties
    )
}
