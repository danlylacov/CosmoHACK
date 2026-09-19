# CosmoHACK Orbit API

REST API для расчёта траекторий МКС и объектов из скрининга CelesTrak SOCRATES.  
Координаты возвращаются в системе TEME, единицы — километры и км/с.

---

## Запуск

```bash
cd MMOD
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Endpoints

### GET /health

Проверка работоспособности сервиса.

**Ответ:**
```json
{ "status": "ok" }
```

---

### POST /api/v1/orbits/positions

Возвращает координаты МКС и объектов-кандидатов сближения (по данным CelesTrak SOCRATES)
с шагом **1 минута** в заданном временном интервале.

#### Запрос

```json
{
  "start_time": "2026-09-19T00:00:00Z",
  "end_time":   "2026-09-19T00:03:00Z"
}
```

| Поле | Тип | Описание |
|---|---|---|
| `start_time` | string (ISO 8601) | Начало интервала, включительно. Обязателен часовой пояс. |
| `end_time`   | string (ISO 8601) | Конец интервала, не включается. Обязателен часовой пояс. |

**Правила валидации:**
- Оба поля должны содержать часовой пояс; значения приводятся к UTC.
- `end_time` должен быть позже `start_time`.
- Максимальная продолжительность — **24 часа**.
- Шаг — ровно **60 секунд**; количество samples = `ceil((end − start) / 60)`.

#### Ответ `200 OK`

```json
{
  "request": {
    "start_time": "2026-09-19T00:00:00Z",
    "end_time":   "2026-09-19T00:03:00Z",
    "step_seconds": 60
  },
  "coordinate_frame": "TEME",
  "position_unit": "km",
  "velocity_unit": "km/s",
  "source": {
    "name": "CelesTrak SOCRATES Plus / CelesTrak GP",
    "retrieved_at": "2026-09-19T12:22:01Z"
  },
  "samples": [
    {
      "timestamp": "2026-09-19T00:00:00Z",
      "iss": {
        "norad_id": 25544,
        "position_km":  { "x": 2672.924, "y": -3498.42,  "z": 5167.163 },
        "velocity_km_s": { "x": 6.854,   "y": 3.118,    "z": -1.435  }
      },
      "screened_objects": [
        {
          "norad_id": 100057,
          "name": "SOYUZ-MS 29",
          "object_type": null,
          "position_km":  { "x": 2670.1, "y": -3495.3, "z": 5163.7 },
          "velocity_km_s": { "x": 6.851, "y": 3.115,   "z": -1.431 }
        }
      ]
    }
  ],
  "data_quality": {
    "status": "COMPLETE",
    "warnings": [],
    "elements_epoch": "2026-09-18T18:54:33Z",
    "calculated_at":  "2026-09-19T12:22:02Z"
  }
}
```

| Поле | Описание |
|---|---|
| `samples` | Массив минутных точек, отсортированных по времени. |
| `samples[].iss` | Позиция и скорость МКС (NORAD 25544) в системе TEME. |
| `samples[].screened_objects` | Все объекты из скрининга SOCRATES, для которых удалось получить орбитальные элементы и распространить траекторию. Пустой массив, если кандидатов нет или ни один не рассчитан. |
| `data_quality.status` | `COMPLETE` — всё рассчитано; `PARTIAL` — МКС есть, один или несколько кандидатов пропущены; `NO_CANDIDATES` — SOCRATES не вернул кандидатов. |
| `data_quality.elements_epoch` | Самый старый epoch среди орбитальных элементов, участвовавших в расчёте. |
| `source.retrieved_at` | Время последнего реального обращения к CelesTrak. |

#### Коды ошибок

| HTTP | Код | Когда |
|---|---|---|
| 422 | `VALIDATION_ERROR` | Неверный формат дат, нет часового пояса, `end ≤ start`, интервал > 24 ч. |
| 422 | `PROPAGATION_ERROR` | Ошибка SGP4 при расчёте траектории МКС. |
| 422 | `STALE_ORBITAL_ELEMENTS` | Орбитальные элементы устарели сверх допустимого предела. |
| 502 | `ORBITAL_ELEMENTS_ERROR` | Не удалось получить орбитальные элементы МКС. |
| 503 | `SOURCE_UNAVAILABLE` | CelesTrak SOCRATES недоступен, и кеша нет. |
| 500 | `ORBIT_SERVICE_ERROR` | Непредвиденная внутренняя ошибка. |

Тело ошибки:
```json
{
  "error": {
    "code": "SOURCE_UNAVAILABLE",
    "message": "SOCRATES unavailable: ...",
    "details": null
  }
}
```

#### Кеширование

| Данные | TTL |
|---|---|
| Список кандидатов SOCRATES | 2 часа |
| Орбитальные элементы (OMM JSON) | 2 часа |

При повторных запросах в течение TTL внешние HTTP-обращения к CelesTrak не выполняются.

---

## Источники данных

| Источник | URL | Что используется |
|---|---|---|
| CelesTrak SOCRATES Plus | `https://celestrak.org/SOCRATES/table-socrates.php` | Список объектов-кандидатов сближения с МКС |
| CelesTrak GP (OMM JSON) | `https://celestrak.org/NORAD/elements/gp.php?FORMAT=json` | Орбитальные элементы по NORAD ID |
