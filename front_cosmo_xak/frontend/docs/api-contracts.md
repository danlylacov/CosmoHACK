# API contracts — EVA decision support

Источник правды для фронтенда и бэкенда. Версия контракта совпадает с
`algorithm_version`: **eva-risk-0.9.2**.

Базовый префикс: `/api/v1`. Все времена — **ISO 8601 UTC** (`...Z`).
Неизвестное поле — `null`, не `undefined`. Все enum — строковые и фиксированные.

Переключение моков: `frontend/src/config.js` → `USE_MOCKS` / `BASE_URL`.
Компоненты UI не знают, мок это или живой бэкенд.

---

## Эндпоинты

| Метод | Путь | Назначение |
| --- | --- | --- |
| `POST` | `/api/v1/analyze` | Расчёт рисков и окон ВКД |
| `GET` | `/api/v1/iss/tle` | Текущий TLE МКС |
| `GET` | `/api/v1/sources/status` | Статус внешних источников |
| `POST` | `/api/v1/sources/refresh` | Принудительное обновление источников |
| `GET` | `/api/v1/report/{query_id}` | Полный отчёт (тот же объект, что UI) |

---

## Общие правила

1. Все времена — ISO 8601 UTC.
2. Неизвестное / неприменимое поле — `null`.
3. Enum только из списков ниже. Новые значения требуют bump контракта.
4. Ошибка (HTTP 4xx/5xx или отказ источника):

```json
{
  "status": "error",
  "message": "Источник NOAA SWPC недоступен. Расчёт risk_sw не выполнен.",
  "code": "SOURCE_UNAVAILABLE",
  "algorithm_version": "eva-risk-0.9.2"
}
```

Коды: `SOURCE_UNAVAILABLE`, `VALIDATION_ERROR`, `NETWORK`, `REPORT_NOT_FOUND`, `INVALID_JSON`.

5. В **каждом** успешном ответе есть `algorithm_version`.
6. В экспорте — версия алгоритма и версии/метки источников (`fetched_at`, `status`).

---

## `POST /api/v1/analyze`

### Запрос

```ts
type AnalyzeRequest = {
  mode: "current" | "historical";
  start: string;
  duration: number;
  period: number;
  historical_intent: "review" | "replay" | null; // null в current
  cutoff_time: string | null; // обязательно для replay, иначе null
  disabled_sources: string[];
  frozen_sources: string[];
};
```

Валидация (дублируется на фронте):

- `duration ∈ [1, 8]`, `period ∈ [1, 24]`, `duration ≤ period`
- `mode = historical` → `start` в `2024-05-01T00:00:00Z` … `2024-06-30T23:59:59Z`
- `historical_intent = replay` → `cutoff_time` задан и не позже `start`
- отключённый источник не даёт «нулевой риск»: `availability = disabled | insufficient_data`

`POST /api/v1/sources/refresh` принимает `{ frozen_sources: string[] }` — замороженные не обновлять, вернуть прежний `fetched_at`.

### Исторические функции

| intent | Смысл | cutoff | verification |
| --- | --- | --- | --- |
| `replay` | прогноз из прошлого | обязателен | поздние наблюдения, `used_in_calculation: false` |
| `review` | разбор архива | `null` | `null`; орбита может быть `geometry_kind: reconstruction` |

`orbit.geometry_kind`: `operational` | `reconstruction`. Историческая орбита не подменяется текущей.

Каждый `warning` содержит `source_url` (или `null`, если первоисточник неизвестен — тогда `unsuitable_for_replay` у источника).

`recommendation.tie: true` если окна равнозначны.

`risk_sw.availability` / `risk_mmod.availability`: `ok` | `insufficient_data` | `disabled`.

### Ответ `AnalyzeResponse`

Единая схема для Simple и Advanced. Simple показывает подмножество полей.

```ts
type AnalyzeStatus = "success" | "partial" | "error";

type AnalyzeResponse = {
  query_id: string;
  status: AnalyzeStatus;
  algorithm_version: string;
  request: {
    start: string;
    duration: number;
    period: number;
    mode: "current" | "historical";
    cutoff_time: string | null; // только historical, иначе null
  };
  orbit: {
    tle_source: string;
    tle_epoch: string;
    tle_age_hours: number;
    iss_trajectory: Array<{
      t: string;
      lat: number;
      lon: number;
      alt_km: number;
      in_shadow: boolean;
    }>;
    shadow_intervals: Array<[string, string]>;
  };
  risk_sw: {
    risk_sw: number; // 0..1
    components: { sep: number; cme: number; kp: number; flare: number };
    time_series: Array<{ t: number; risk: number }>; // t = часы от start
    metrics: { kp: number; sep_10mev: number; cme_speed: number };
    warnings: Warning[];
  };
  risk_mmod: {
    risk_mmod: number;
    components: { conjunction: number; meteor: number };
    time_series: Array<{ t: number; risk: number }>;
    metrics: {
      miss_distance: number; // км
      pc: number;
      tca: string;
    };
    warnings: Warning[];
  };
  windows: Array<{
    start: string;
    end: string;
    score: number;
    risk_sw: number;
    risk_mmod: number;
    overlap_sw: number;
    overlap_mmod: number;
    completeness: number;
    requires_check: boolean;
  }>;
  recommendation: {
    window: { start: string; end: string } | null;
    score: number;
    reason: string;
    limitations: string;
  };
  metadata: {
    completeness: number;
    confidence_sw: "низкая" | "средняя" | "высокая";
    confidence_mmod: "низкая" | "средняя" | "высокая";
    algorithm_version: string;
    sources: Array<{
      name: string;
      url: string;
      fetched_at: string;
      age: string;
      status: "ok" | "warn" | "error" | "stale";
    }>;
  };
};

type Warning = {
  id: string;
  type: "observation" | "forecast" | "computed";
  mechanism: "radiation" | "mmod" | "illumination";
  title: string;
  source: string;
  published_at: string;
  event_time: string | null;
  period: { start: string; end: string } | null;
  value: number | null;
  unit: string | null;
  impact: string;
  rule: string;
  limitations: string;
  confidence: "low" | "medium" | "high";
  intersects_window: boolean;
};
```

### Семантика `status`

| status | UI |
| --- | --- |
| `success` | Можно интерпретировать риски, если `completeness` высокая и нет `error` источников |
| `partial` | Часть источников недоступна. **Запрещено** показывать «всё безопасно» |
| `error` | Расчёт не выполнен. Сообщение + «Повторить» |

`recommendation.window === null` → текст:
«Недостаточно оснований для выбора. Требуется дополнительная проверка.»

Типы: `frontend/src/api/contracts.ts`.

---

## `GET /api/v1/iss/tle`

```ts
type TleResponse = {
  status: "success";
  algorithm_version: string;
  name: string;
  norad_id: number;
  source: string;
  epoch: string;
  age_hours: number;
  line1: string;
  line2: string;
};
```

---

## `GET /api/v1/sources/status` и `POST /api/v1/sources/refresh`

Одинаковая схема ответа:

```ts
type SourcesStatusResponse = {
  status: "success" | "partial" | "error";
  algorithm_version: string;
  completeness: number;
  confidence_sw: "низкая" | "средняя" | "высокая";
  confidence_mmod: "низкая" | "средняя" | "высокая";
  refreshed_at: string;
  sources: AnalyzeResponse["metadata"]["sources"];
};
```

`stale` — источник устарел, UI показывает давность (`age`) и не трактует данные как свежие.

---

## `GET /api/v1/report/{query_id}`

Возвращает сохранённый `AnalyzeResponse` для выгрузки. 404 → `REPORT_NOT_FOUND`.

Экспорт UI обязан содержать те же числа, что на экране, плюс `algorithm_version` и метки источников.

---

## Моки

| Файл | Сценарий |
| --- | --- |
| `src/api/mocks/analyze-response.json` | Успех, historical 2024-06-15T08:00:00Z, 6 ч / 12 ч |
| `src/api/mocks/error-response.json` | Ошибка источника NOAA SWPC |
| `src/api/mocks/sources-status.json` | Смешанный статус, Space-Track CDM = `stale` |
| `src/api/mocks/tle-response.json` | TLE ISS (25544) |

Демо-сценарий **partial**: `mode=historical` и `start` начинается с `2024-05-15` —
NOAA помечается `error`, `recommendation.window = null`.

Перегенерация траектории: `npm run mocks`.

---

## Версионирование

- Ответ API: поле `algorithm_version`.
- Экспорт ZIP: `manifest.json` → `algorithm_version` + `source_versions[]`.
- Смена enum, единиц или смысла поля = новая minor/patch версия контракта и запись в этом файле.
