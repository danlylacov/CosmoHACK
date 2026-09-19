# data-aggregator

Сервис слушает порт `8088`, все эндпоинты доступны под context-path `/ch/data-aggregator`.

## Запуск через Dockerfile

Собрать образ и запустить контейнер с пробросом порта наружу:

```bash
docker build -t data-aggregator:local .
docker run --rm -p 8088:8088 data-aggregator:local
```

Либо одной командой через исполняемый файл [run.sh](run.sh), который сам соберёт образ и поднимет контейнер (через docker-compose, порт 8088 пробрасывается на хост):

```bash
./run.sh
```

## docker-compose

Конфигурация лежит в [docker-compose.yml](docker-compose.yml):

```yaml
services:
  data-aggregator:
    build:
      context: .
      dockerfile: Dockerfile
    image: data-aggregator:local
    ports:
      - "8088:8088"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8088/ch/data-aggregator/health"]
```

Запуск/остановка:

```bash
docker compose up --build -d
docker compose down
```

## Health-check

```
GET http://localhost:8088/ch/data-aggregator/health
```

Ответ:

```json
{"status": "UP"}
```

## OpenAPI / Swagger

Документация генерируется автоматически (springdoc-openapi):

- OpenAPI-спецификация: `http://localhost:8088/ch/data-aggregator/v3/api-docs`
- Swagger UI: `http://localhost:8088/ch/data-aggregator/swagger-ui.html`
