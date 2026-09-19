package ru.viktorgezz.data_aggregator.eva.client

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import ru.viktorgezz.data_aggregator.common.UpstreamResult
import ru.viktorgezz.data_aggregator.eva.config.EvaClientProperties
import ru.viktorgezz.data_aggregator.eva.controller.EvaUpstreamUnavailableException
import ru.viktorgezz.data_aggregator.eva.dto.*
import tools.jackson.databind.json.JsonMapper
import java.io.IOException
import java.net.HttpURLConnection.HTTP_BAD_GATEWAY

private val JSON_MEDIA_TYPE = "application/json".toMediaType()

class EvaApiClient(
    private val okHttpClient: OkHttpClient,
    private val jsonMapper: JsonMapper,
    private val properties: EvaClientProperties,
) {

    fun health(): UpstreamResult<EvaHealthResponse, EvaErrorResponse> =
        execute(Request.Builder().url(properties.urlHealth.toHttpUrl()).get().build(), EvaHealthResponse::class.java)

    fun windows(request: EvaWindowsRequest): UpstreamResult<EvaWindowsResponse, EvaErrorResponse> =
        execute(postRequest(properties.urlWindows.toHttpUrl(), request), EvaWindowsResponse::class.java)

    private fun postRequest(url: HttpUrl, body: Any): Request =
        Request.Builder()
            .url(url)
            .post(jsonMapper.writeValueAsBytes(body).toRequestBody(JSON_MEDIA_TYPE))
            .build()

    private fun <T> execute(request: Request, responseType: Class<T>): UpstreamResult<T, EvaErrorResponse> {
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
                    val error = runCatching { jsonMapper.readValue(bodyBytes, EvaErrorResponse::class.java) }
                        .recoverCatching { jsonMapper.readValue(bodyBytes, EvaHTTPValidationError::class.java).toErrorResponse() }
                        .getOrElse { fallbackError(bodyBytes) }
                    UpstreamResult.Failure(response.code, error)
                }
            }
        } catch (e: IOException) {
            throw EvaUpstreamUnavailableException(
                "Не удалось обратиться к вышестоящему сервису по адресу ${request.url}",
                e,
            )
        }
    }

    private fun EvaHTTPValidationError.toErrorResponse(): EvaErrorResponse =
        EvaErrorResponse(
            EvaErrorBody(
                code = "VALIDATION_ERROR",
                message = detail.joinToString("; ") { "${it.loc.joinToString(".")}: ${it.msg}" }
                    .ifEmpty { "Ошибка валидации запроса" },
                details = mapOf("validation_errors" to detail),
            ),
        )

    private fun fallbackError(bodyBytes: ByteArray): EvaErrorResponse =
        EvaErrorResponse(
            EvaErrorBody(
                code = "EVA_SERVICE_ERROR",
                message = "Вышестоящий сервис вернул тело ответа, не соответствующее контракту ошибки",
                details = mapOf("raw_body" to String(bodyBytes)),
            ),
        )
}
