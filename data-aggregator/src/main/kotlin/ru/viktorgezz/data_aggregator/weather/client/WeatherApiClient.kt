package ru.viktorgezz.data_aggregator.weather.client

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import ru.viktorgezz.data_aggregator.common.UpstreamResult
import ru.viktorgezz.data_aggregator.weather.config.WeatherClientProperties
import ru.viktorgezz.data_aggregator.weather.controller.WeatherUpstreamUnavailableException
import ru.viktorgezz.data_aggregator.weather.dto.SpaceWeatherResponse
import ru.viktorgezz.data_aggregator.weather.dto.WeatherErrorResponse
import tools.jackson.databind.json.JsonMapper
import java.io.IOException
import java.net.HttpURLConnection.HTTP_BAD_GATEWAY
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter

class WeatherApiClient(
    private val okHttpClient: OkHttpClient,
    private val jsonMapper: JsonMapper,
    private val properties: WeatherClientProperties,
) {

    fun spaceWeather(start: OffsetDateTime, end: OffsetDateTime): UpstreamResult<SpaceWeatherResponse, WeatherErrorResponse> =
        execute(spaceWeatherRequest(start, end), SpaceWeatherResponse::class.java)

    private fun spaceWeatherRequest(start: OffsetDateTime, end: OffsetDateTime): Request {
        val url = properties.urlSpaceWeather.toHttpUrl().newBuilder()
            .addQueryParameter("start", DateTimeFormatter.ISO_INSTANT.format(start))
            .addQueryParameter("end", DateTimeFormatter.ISO_INSTANT.format(end))
            .build()
        return Request.Builder().url(url).get().build()
    }

    private fun <T> execute(request: Request, responseType: Class<T>): UpstreamResult<T, WeatherErrorResponse> {
        try {
            okHttpClient.newCall(request).execute().use { response ->
                val bodyBytes = response.body?.bytes() ?: ByteArray(0)
                return if (response.isSuccessful) {
                    runCatching { jsonMapper.readValue(bodyBytes, responseType) }
                        .fold(
                            onSuccess = { UpstreamResult.Success(response.code, it) },
                            onFailure = { UpstreamResult.Failure(HTTP_BAD_GATEWAY, fallbackError(bodyBytes)) },
                        )
                } else {
                    val error = runCatching { jsonMapper.readValue(bodyBytes, WeatherErrorResponse::class.java) }
                        .getOrElse { fallbackError(bodyBytes) }
                    UpstreamResult.Failure(response.code, error)
                }
            }
        } catch (e: IOException) {
            throw WeatherUpstreamUnavailableException(
                "Не удалось обратиться к вышестоящему сервису по адресу ${request.url}",
                e,
            )
        }
    }

    private fun fallbackError(bodyBytes: ByteArray): WeatherErrorResponse =
        WeatherErrorResponse(
            detail = "Вышестоящий сервис вернул тело ответа, не соответствующее контракту ошибки: ${String(bodyBytes)}",
        )
}
