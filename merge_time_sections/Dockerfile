FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PORT=8001 \
    CONJUNCTION_API_BASE_URL=http://mmod:8000 \
    SPACE_WEATHER_BASE_URL=http://sepnet-sgr:8002 \
    WEATHER_ENABLED=0 \
    UPSTREAM_TIMEOUT_SECONDS=180

WORKDIR /app

RUN adduser --disabled-password --gecos "" --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
COPY . /app/merge_time_sections/

USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"

CMD ["uvicorn", "merge_time_sections.app:app", "--host", "0.0.0.0", "--port", "8001"]
