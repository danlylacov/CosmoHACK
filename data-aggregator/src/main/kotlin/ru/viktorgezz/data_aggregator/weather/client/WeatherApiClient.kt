package ru.viktorgezz.data_aggregator.weather.client

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import ru.viktorgezz.data_aggregator.common.UpstreamResult
import ru.viktorgezz.data_aggregator.weather.config.WeatherClientProperties
import ru.viktorgezz.data_aggregator.weather.dto.*
import tools.jackson.databind.json.JsonMapper
import java.io.IOException
import java.net.HttpURLConnection.HTTP_BAD_GATEWAY

private val JSON_MEDIA_TYPE = "application/json".toMediaType()

class WeatherApiClient(
    private val okHttpClient: OkHttpClient,
    private val jsonMapper: JsonMapper,
    private val properties: WeatherClientProperties,
) {

    fun health(): UpstreamResult<HealthResponse> =
        execute(Request.Builder().url(properties.urlHealth.toHttpUrl()).get().build(), HealthResponse::class.java)

    fun positions(request: PositionsRequest): UpstreamResult<PositionsResponse> =
        execute(postRequest(properties.urlPositions.toHttpUrl(), request), PositionsResponse::class.java)

    fun conjunctionDistances(request: ConjunctionRequest): UpstreamResult<ConjunctionResponse> =
        execute(postRequest(properties.urlDistances.toHttpUrl(), request), ConjunctionResponse::class.java)

    private fun postRequest(url: HttpUrl, body: Any): Request =
        Request.Builder()
            .url(url)
            .post(jsonMapper.writeValueAsBytes(body).toRequestBody(JSON_MEDIA_TYPE))
            .build()

    private fun <T> execute(request: Request, responseType: Class<T>): UpstreamResult<T> {
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
                    val error = runCatching { jsonMapper.readValue(bodyBytes, ErrorResponse::class.java) }
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

    private fun fallbackError(bodyBytes: ByteArray): ErrorResponse =
        ErrorResponse(
            ErrorBody(
                code = ErrorCode.ORBIT_SERVICE_ERROR,
                message = "Вышестоящий сервис вернул тело ответа, не соответствующее контракту ошибки",
                details = mapOf("raw_body" to String(bodyBytes)),
            ),
        )
}
