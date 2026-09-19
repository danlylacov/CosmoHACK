package ru.viktorgezz.data_aggregator.config

import com.fasterxml.jackson.annotation.JsonInclude
import org.springframework.boot.jackson.autoconfigure.JsonMapperBuilderCustomizer
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import tools.jackson.databind.DeserializationFeature
import tools.jackson.databind.cfg.DateTimeFeature
import tools.jackson.module.kotlin.KotlinModule

@Configuration
class AppConfig {

    /**
     * Донастраивает автоконфигурированный Spring Boot'ом [tools.jackson.databind.json.JsonMapper].
     *
     * Применяемые настройки:
     * - [KotlinModule] — корректная (де)сериализация Kotlin data class'ов: non-null поля,
     *   default-значения параметров конструктора, поддержка `data class` без no-args конструктора.
     * - [DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES] выключен — незнакомые поля во входящем
     *   JSON не приводят к ошибке десериализации, а просто игнорируются.
     * - [DeserializationFeature.FAIL_ON_IGNORED_PROPERTIES] выключен — поля, явно помеченные
     *   как игнорируемые (например через `@JsonIgnore`), не вызывают ошибку, даже если присутствуют во входящем JSON.
     * - [DateTimeFeature.WRITE_DATES_AS_TIMESTAMPS] выключен — даты и время сериализуются
     *   в читаемый ISO-8601 формат (строка), а не в виде числового timestamp'а.
     * - Property inclusion — поля со значением `null` исключаются из результирующего JSON
     *   как при сериализации самого объекта (value inclusion), так и при сериализации
     *   элементов коллекций/Map (content inclusion).
     */
    @Bean
    fun jsonMapperBuilderCustomizer(): JsonMapperBuilderCustomizer =
        JsonMapperBuilderCustomizer { builder ->
            builder
                .addModule(KotlinModule.Builder().build())
                .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .disable(DeserializationFeature.FAIL_ON_IGNORED_PROPERTIES)
                .disable(DateTimeFeature.WRITE_DATES_AS_TIMESTAMPS)
                .changeDefaultPropertyInclusion { incl ->
                    incl
                        .withValueInclusion(JsonInclude.Include.NON_NULL)
                        .withContentInclusion(JsonInclude.Include.NON_NULL)
                }
        }
}