#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "Собираю образ и поднимаю data-aggregator (docker compose up --build -d)..."
docker compose up --build -d

cat <<EOF

Сервис поднят и слушает порт 8088 на хосте.

  Health:      http://localhost:8088/ch/data-aggregator/health
  Swagger UI:  http://localhost:8088/ch/data-aggregator/swagger-ui.html
  OpenAPI:     http://localhost:8088/ch/data-aggregator/v3/api-docs

  Логи:        docker compose logs -f data-aggregator
  Остановить:  docker compose down
EOF
