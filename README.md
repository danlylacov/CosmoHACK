# CosmoHACK

В репозитории находятся Three.js-визуализация, исследовательские скрипты `MMOD`
и FastAPI backend для расчёта положения МКС и объектов сближения.

## Orbit API

Backend работает на Python 3.12 и предоставляет:

- `POST /api/v1/orbits/positions` — состояния МКС и ближайшего найденного объекта;
- `POST /api/v1/conjunctions/distances` — расстояние, относительную скорость и
  интервалы прохождения порога сближения;
- `GET /health` — проверка готовности приложения;
- `GET /` — переход к Swagger UI;
- OpenAPI — `/docs` и `/openapi.json`.

Время запроса задаётся ISO 8601 timestamp с обязательным часовым поясом.
Вычисления выполняются для полуинтервала `[start_time, end_time)` с шагом одна
секунда, максимум за 24 часа. Ответы нормализуются в UTC. Положения и скорости
распространяются напрямую библиотекой `sgp4` в TEME, в km и km/s.

`critical_distance_km` — только порог сближения для дополнительной проверки,
а не вероятность столкновения. Точка критична строго при
`distance_km < critical_distance_km`. Без порога `is_critical` равен `null`, а
`critical_intervals` — пустой массив.

### Источник и ограничения

`CelesTrakOrbitalElementsProvider` использует SOCRATES для предварительного
скрининга кандидатов МКС (NORAD 25544), затем загружает их GP/TLE и SATCAT
метаданные из CelesTrak. Поэтому «ближайший объект» означает ближайший среди
текущих кандидатов SOCRATES, а не доказанный глобальный минимум по всему
мировому каталогу. SOCRATES покрывает только собственное актуальное окно
прогноза.

Снимок элементов кешируется в памяти процесса на 1 час. Значения настраиваются:

- `ELEMENTS_CACHE_TTL_SECONDS` — TTL кеша, по умолчанию `3600`;
- `ELEMENTS_MAX_AGE_HOURS` — допустимое удаление расчёта от epoch, по умолчанию
  `168`;
- `SOURCE_TIMEOUT_SECONDS` — timeout внешних запросов, по умолчанию `30`;
- `SOCRATES_MAX_RECORDS` — максимум кандидатов, по умолчанию `100`;
- `GZIP_MINIMUM_SIZE` — порог gzip, по умолчанию `1000` байт.

Если обновление источника не удалось, уже имеющийся снимок может быть
использован с предупреждением. Без кеша возвращается `503`. Устаревшие или
невалидные кандидаты исключаются с диагностикой; устаревшие элементы МКС или
ошибка её распространения завершают запрос. Если валидного кандидата нет,
`nearest_object`, расстояние и скорость равны `null`, а `data_quality.status`
равен `NO_CANDIDATES`. Частичный ряд помечается `PARTIAL`; случайные или
синтетические значения не подставляются.

### Архитектура

- `backend/app/api` — routers, dependency injection и представление ответов;
- `backend/app/schemas` — Pydantic request/response контракты;
- `backend/app/providers` — заменяемый `OrbitalElementsProvider` и CelesTrak;
- `backend/app/cache` — конкурентно-безопасный TTL-кеш снимка;
- `backend/app/services/propagation.py` — векторный SGP4;
- `backend/app/services/nearest.py` — евклидовы нормы и nearest search;
- `backend/app/services/critical_intervals.py` — TCA и критические интервалы;
- `backend/app/core/errors.py` — единый формат ошибок.

При смене ближайшего NORAD внутри критического периода текущий интервал
закрывается, а новый начинается на той же секунде. Конец интервала — первая
секунда с расстоянием больше либо равным порогу или `end_time` запроса.

### Установка и запуск

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Проверки из каталога `backend`:

```powershell
ruff format --check .
ruff check .
pytest
```

Тесты используют только fixture-провайдеры и не обращаются к интернету.

## Существующая визуализация

Статический frontend можно открыть через локальный HTTP-сервер:

```powershell
python -m http.server 8000
```

Затем перейти на `http://localhost:8000/frontend/`. Текущий frontend читает
подготовленный файл `MMOD/trajectories.json`; подключение его к API не входит в
этот backend-модуль.