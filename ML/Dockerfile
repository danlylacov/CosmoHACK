# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc g++ pkg-config libnetcdf-dev libhdf5-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY api/requirements.txt api/requirements.txt
COPY models/sepnet_sgr/requirements.txt models/sepnet_sgr/requirements.txt
COPY models/sepnet/requirements.txt models/sepnet/requirements.txt
COPY models/lstm/requirements.txt models/lstm/requirements.txt
COPY data/tools/space_weather_downloader/requirements.txt downloader-requirements.txt

RUN pip install --upgrade pip \
    && pip install fastapi "uvicorn[standard]" "numpy>=1.24,<3" "pandas>=2.0,<3" \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r downloader-requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PORT=8002

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnetcdf-dev libhdf5-dev ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --disabled-password --gecos "" --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
COPY api ./api
COPY models ./models
COPY data ./data

RUN mkdir -p /app/data/datasets \
    && ln -s /opt/venv /app/data/tools/space_weather_downloader/.venv \
    && chmod +x /app/data/run.sh /app/data/tools/*/run.sh \
    && chown -R appuser:appuser /app/data/datasets

USER appuser

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3)"

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8002"]
