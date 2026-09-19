package ru.viktorgezz.data_aggregator.weather.client

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import ru.viktorgezz.data_aggregator.common.UpstreamResult
import ru.viktorgezz.data_aggregator.weather.config.WeatherClientProperties
import ru.viktorgezz.data_aggregator.weather.controller.WeatherUpstreamUnavailableException
import ru.viktorgezz.data_aggregator.weather.dto.ForecastRequest
import ru.viktorgezz.data_aggregator.weather.dto.WeatherErrorResponse
import ru.viktorgezz.data_aggregator.weather.dto.WeatherHTTPValidationError
import tools.jackson.databind.json.JsonMapper
import java.io.IOException
import java.net.HttpURLConnection.HTTP_BAD_GATEWAY

private val JSON_MEDIA_TYPE = "application/json".toMediaType()

class WeatherApiClient(
    private val okHttpClient: OkHttpClient,
    private val jsonMapper: JsonMapper,
    private val properties: WeatherClientProperties,
) {

    fun health(): UpstreamResult<Any, WeatherErrorResponse> =
        execute(Request.Builder().url(properties.urlHealth.toHttpUrl()).get().build(), Any::class.java)

    fun forecast(request: ForecastRequest): UpstreamResult<Any, WeatherErrorResponse> =
        execute(forecastRequest(request), Any::class.java)

    private fun forecastRequest(body: ForecastRequest): Request =
        Request.Builder()
            .url(properties.urlForecast.toHttpUrl())
            .post(jsonMapper.writeValueAsBytes(body).toRequestBody(JSON_MEDIA_TYPE))
            .build()

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
                    val error = runCatching { jsonMapper.readValue(bodyBytes, WeatherHTTPValidationError::class.java).toErrorResponse() }
                        .recoverCatching { jsonMapper.readValue(bodyBytes, WeatherErrorResponse::class.java) }
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

    private fun WeatherHTTPValidationError.toErrorResponse(): WeatherErrorResponse =
        WeatherErrorResponse(
            detail = detail.joinToString("; ") { "${it.loc.joinToString(".")}: ${it.msg}" }
                .ifEmpty { "Ошибка валидации запроса" },
        )

    private fun fallbackError(bodyBytes: ByteArray): WeatherErrorResponse =
        WeatherErrorResponse(
            detail = "Вышестоящий сервис вернул тело ответа, не соответствующее контракту ошибки: ${String(bodyBytes)}",
        )
}
