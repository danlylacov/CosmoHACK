# ВКД · поддержка решений (frontend)

Исследовательский прототип для **КосмоХакатон 2026**. Это не система допуска к ВКД.

## Запуск

```bash
cd frontend
npm install
npm run dev
npm test
```

http://localhost:5173 — Simple `#/simple`, Advanced `#/advanced`.

## Подключение бэкенда

В `src/config.js`:

```js
export const USE_MOCKS = false;
export const BASE_URL = "https://your-api.example";
```

Контракт: [docs/api-contracts.md](docs/api-contracts.md) и `src/api/contracts.ts`. UI уже шлёт `historical_intent`, `cutoff_time`, `disabled_sources`, `frozen_sources` и ждёт `verification`, `availability`, `source_url`, `geometry_kind`.

Траектория МКС (TEME → lat/lon, тень, глобус): [docs/trajectory.md](docs/trajectory.md).

Все fetch только из `src/api/client.js`.

Живые сервисы (Vite proxy, мок analyze не трогает эти пути):

- Orbit `POST /orbit-api/api/v1/orbits/positions` и `/conjunctions/distances` → `:8000`
- EVA `POST /eva-api/api/v1/eva/windows` → `:8001`
- SW forecast `POST /forecast-api/forecast` `{ origin }` → `:8002`. Критерий выхода — `eva_allowed`; полосы на графиках радиации и MMOD.

## Демо

1. Historical + **Replay**, 2024-06-15 08:00 UTC, 6 ч / 12 ч → расчёт, cutoff, блок «поздние наблюдения не в расчёте».
2. **Разбор архива** — полный архив, геометрия reconstruction, без претензии на replay.
3. Дата **2024-05-15** → `partial`, окно не выбирается.
4. В источниках: «отключить» NOAA → SW = н/д, не «безопасно». «Заморозить» — refresh не обновляет этот ряд.
5. Current — автопроверка `/health` живых API каждые 30 с.
6. Смена длительности 8→4 → баннер «это смена плана».
7. Карточка предупреждения → реальный URL первоисточника. Экспорт ZIP содержит cutoff и версии источников.

Поля времени — дата + время + бейдж UTC, не локальный datetime-local.

Освещённость на глобусе — условие работ, не третий механизм риска. Механизмы: SW и MMOD.

## Disclaimer

Исследовательский прототип поддержки решений. Допуск к реальной ВКД остаётся за уполномоченными специалистами.
