#!/usr/bin/env python3
"""HTTP wrapper around SEPNET-SGR inference.

Uses the bundled CSV dataset when it is already present. data/run.sh runs only
if results.csv is missing, or when SEPNET_FORCE_DOWNLOAD=1.
Does not write model output files and does not change model code.

Run (from ML/):
  PYTHONPATH=. uvicorn api.app:app --host 0.0.0.0 --port 8002
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
_ML_ROOT = _DIR.parent
if str(_ML_ROOT) not in sys.path:
    sys.path.insert(0, str(_ML_ROOT))

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from models.sepnet_sgr.fallback import parse_time
from models.sepnet_sgr.infer import forecast

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("sepnet_sgr_api")

DEFAULT_CHECKPOINT = _ML_ROOT / "models" / "sepnet_sgr" / "artifacts" / "model.pt"
DEFAULT_DATA = _ML_ROOT / "data" / "datasets"
DOWNLOAD_SCRIPT = _ML_ROOT / "data" / "run.sh"
DEFAULT_PORT = 8002
HISTORY_DAYS = 3
DEFAULT_DOWNLOAD_TIMEOUT = 900

_infer_lock = asyncio.Lock()


class ForecastRequest(BaseModel):
    origin: str = Field(..., min_length=1)
    cutoff: str | None = None
    data: str | None = None
    checkpoint: str | None = None
    policy: str | None = None

    model_config = {"json_schema_extra": {
        "example": {
            "origin": "2026-09-18T00:00:00Z",
            "cutoff": None,
            "data": None,
            "checkpoint": None,
            "policy": None,
        }
    }}


app = FastAPI(
    title="CosmoHACK SEPNET-SGR API",
    version="1.0.0",
    description="Thin HTTP wrapper around SEPNET-SGR forecast(). Returns forecast.json.",
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _err(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


def _env_path(name: str, default: Path | None) -> Path | None:
    raw = os.environ.get(name)
    if raw:
        return Path(raw)
    return default


def _resolve_path(value: str | None, fallback: Path | None) -> Path | None:
    if value:
        return Path(value)
    return fallback


def _missing(path: Path | None, label: str, *, directory_ok: bool = False) -> JSONResponse | None:
    if path is None:
        return _err(422, "VALIDATION_ERROR", f"{label} is required")
    if not path.exists():
        return _err(422, "VALIDATION_ERROR", f"{label} does not exist: {path}")
    if directory_ok:
        if not (path.is_file() or path.is_dir()):
            return _err(422, "VALIDATION_ERROR", f"{label} must be a file or directory: {path}")
        return None
    if not path.is_file():
        return _err(422, "VALIDATION_ERROR", f"{label} is not a file: {path}")
    return None


def _origin_day(origin: str):
    if origin == "now":
        return datetime.now(timezone.utc).date()
    return parse_time(origin).date()


def _dataset_dir(data: Path) -> Path:
    if data.exists():
        return data if data.is_dir() else data.parent
    return data.parent if data.suffix else data


def _dataset_csv(data: Path) -> Path:
    if data.suffix.lower() == ".csv":
        return data
    return _dataset_dir(data) / "results.csv"


def _dataset_ready(data: Path) -> bool:
    csv_path = _dataset_csv(data)
    try:
        return csv_path.is_file() and csv_path.stat().st_size > 0
    except OSError:
        return False


def _should_refresh(data: Path) -> bool:
    if os.environ.get("SEPNET_FORCE_DOWNLOAD", "").lower() in {"1", "true", "yes"}:
        return True
    return not _dataset_ready(data)


def _refresh_dataset(origin: str, data: Path) -> JSONResponse | None:
    """Pull the origin day plus 72h of history into the dataset used by forecast()."""
    try:
        end = _origin_day(origin)
    except (TypeError, ValueError) as exc:
        return _err(422, "VALIDATION_ERROR", f"Invalid origin: {exc}")
    start = end - timedelta(days=HISTORY_DAYS)
    dataset = _dataset_dir(data).resolve()
    if not DOWNLOAD_SCRIPT.is_file():
        return _err(500, "SOURCE_UNAVAILABLE", f"Downloader is missing: {DOWNLOAD_SCRIPT}")
    timeout = float(os.environ.get("SEPNET_DOWNLOAD_TIMEOUT", str(DEFAULT_DOWNLOAD_TIMEOUT)))
    command = [
        str(DOWNLOAD_SCRIPT),
        "--start", start.isoformat(),
        "--end", end.isoformat(),
        "--out", str(dataset),
    ]
    log.info("[forecast] downloading %s .. %s into %s", start, end, dataset)
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return _err(
            504, "SOURCE_UNAVAILABLE",
            f"Dataset download exceeded {timeout:.0f}s for {start} .. {end}",
        )
    except OSError as exc:
        return _err(502, "SOURCE_UNAVAILABLE", f"Dataset download failed to start: {exc}")
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-4000:] or None
        log.warning("[forecast] download exit %s: %s", result.returncode, detail)
        return _err(
            502, "SOURCE_UNAVAILABLE",
            f"Dataset download exited with code {result.returncode}",
            details=detail,
        )
    if result.stdout:
        log.info("[forecast] download: %s", result.stdout.strip()[-2000:])
    return None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sepnet_sgr"}


@app.post("/forecast")
async def post_forecast(body: ForecastRequest):
    checkpoint = _resolve_path(body.checkpoint, _env_path("SEPNET_CHECKPOINT", DEFAULT_CHECKPOINT))
    data = _resolve_path(body.data, _env_path("SEPNET_DATA", DEFAULT_DATA))
    policy = _resolve_path(body.policy, _env_path("SEPNET_POLICY", None))

    error = _missing(checkpoint, "checkpoint")
    if error is not None:
        return error
    if body.policy or os.environ.get("SEPNET_POLICY"):
        error = _missing(policy, "policy")
        if error is not None:
            return error

    log.info(
        "[forecast] origin=%s cutoff=%s checkpoint=%s data=%s policy=%s",
        body.origin, body.cutoff, checkpoint, data, policy,
    )
    async with _infer_lock:
        if _should_refresh(data):
            error = await asyncio.to_thread(_refresh_dataset, body.origin, data)
            if error is not None:
                return error
        else:
            log.info("[forecast] using existing dataset %s", _dataset_csv(data))
        error = _missing(data, "data", directory_ok=True)
        if error is not None:
            return error
        result = await asyncio.to_thread(
            forecast, checkpoint, data, body.origin, body.cutoff, policy,
        )
    return JSONResponse(content=result)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
