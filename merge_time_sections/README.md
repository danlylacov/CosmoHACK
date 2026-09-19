# CosmoHACK EVA API (`merge_time_sections`)

Отдельный сервис ранжирования окон ВКД. Орбиты **не считает**: ходит в Orbit API (MMOD)
`POST /api/v1/conjunctions/distances`, затем `rank_eva_windows`.

Погода/ML по умолчанию выключены (`WEATHER_ENABLED=0`). Когда 4-й сервис появится —
`WEATHER_ENABLED=1` и `SPACE_WEATHER_BASE_URL`.

## Запуск

Нужен уже запущенный MMOD:

```bash
cd CosmoHACK/MMOD
uvicorn main:app --host 0.0.0.0 --port 8000
```

Затем EVA-сервис:

```bash
cd CosmoHACK
pip install -r merge_time_sections/requirements.txt
uvicorn merge_time_sections.app:app --host 0.0.0.0 --port 8001
```

Swagger EVA: [http://localhost:8001/docs](http://localhost:8001/docs)

## Endpoint

`POST /api/v1/eva/windows`

```json
{
  "start_time": "2026-09-20T03:30:00Z",
  "end_time": "2026-09-20T05:30:00Z",
  "duration_min": 30,
  "step_min": 1,
  "top_k": 5,
  "critical_distance_km": 5.0
}
```

`GET /health` → `{ "status": "ok", "service": "eva" }`

## Порты и env

| Сервис | Порт по умолчанию | Env |
|---|---|---|
| Orbit API (MMOD) | 8000 | `CONJUNCTION_API_BASE_URL=http://127.0.0.1:8000` |
| EVA API (этот) | 8001 | `PORT=8001` |
| Weather/ML (будущий) | 8002 | `SPACE_WEATHER_BASE_URL`, `WEATHER_ENABLED=1` |

Кеш ответа distances в памяти EVA, TTL 2 ч (`DISTANCES_CACHE_TTL_SECONDS`). Смена `duration_min` / `step_min` / `top_k` не дергает MMOD повторно.
