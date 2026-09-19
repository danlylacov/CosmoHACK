#!/usr/bin/env python3
"""GET /space-weather — ряды космической погоды и коэффициент EVA."""

from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

HERE = Path(__file__).resolve().parent
CSV_PATH = HERE.parent / "data_agregate" / "output" / "space_weather.csv"
OPENAPI_PATH = HERE / "openapi.yaml"
INTERVAL_MINUTES = 30

app = FastAPI(
    title="CosmoHACK Space Weather",
    version="0.1.0",
    description=(
        "GET /space-weather: космическая погода на интервале и коэффициент EVA "
        "(1 — можно выходить, 0 — нельзя). Контракт: /openapi.yaml"
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def parse_iso(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def to_number(value: str):
    text = (value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def pick(params: dict, *needles: str):
    for key, value in params.items():
        if all(part in key for part in needles) and value is not None:
            return value
    return None


def geomagnetic_index(params: dict):
    hp = params.get("gfz_Hp30")
    if isinstance(hp, (int, float)):
        return float(hp)
    kp = params.get("gfz_Kp")
    if isinstance(kp, (int, float)):
        return float(kp)
    return None


def proton_factor(pfu: float) -> float:
    if pfu <= 1:
        return 1.0
    return clamp01(1.0 - math.log10(pfu) / 4.0)


def electron_factor(pfu: float) -> float:
    if pfu < 100:
        return 1.0
    return clamp01(1.0 - math.log10(pfu / 100.0) / 3.0)


def eva_coefficient(params: dict) -> float | None:
    factors: list[float] = []
    geo = geomagnetic_index(params)
    if geo is not None:
        factors.append(clamp01(1.0 - max(0.0, geo - 3.0) / 6.0))
    protons = pick(params, "integral_protons", "ge10_MeV")
    if isinstance(protons, (int, float)):
        factors.append(proton_factor(float(protons)))
    electrons = pick(params, "integral_electrons", "ge2_MeV")
    if isinstance(electrons, (int, float)):
        factors.append(electron_factor(float(electrons)))
    if not factors:
        return None
    return round(min(factors), 3)


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for raw in reader:
            time_raw = raw.get("time")
            if not time_raw:
                continue
            parameters = {
                key: to_number(value)
                for key, value in raw.items()
                if key != "time"
            }
            rows.append(
                {
                    "time": parse_iso(time_raw),
                    "parameters": parameters,
                    "eva_coefficient": eva_coefficient(parameters),
                }
            )
        return rows


@app.get("/space-weather")
def get_space_weather(
    start: datetime = Query(..., description="Начало интервала UTC"),
    end: datetime = Query(..., description="Конец интервала UTC"),
):
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        raise HTTPException(status_code=400, detail="start должен быть раньше end")
    if not CSV_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Нет файла данных: {CSV_PATH}",
        )

    records = []
    for row in load_rows(CSV_PATH):
        if start <= row["time"] <= end:
            records.append(
                {
                    "time": iso_z(row["time"]),
                    "eva_coefficient": row["eva_coefficient"],
                    "parameters": row["parameters"],
                }
            )

    scores = [item["eva_coefficient"] for item in records if item["eva_coefficient"] is not None]
    return {
        "start": iso_z(start),
        "end": iso_z(end),
        "interval_minutes": INTERVAL_MINUTES,
        "eva_coefficient": min(scores) if scores else None,
        "records": records,
    }


@app.get("/openapi.yaml", include_in_schema=False)
def openapi_yaml():
    return FileResponse(OPENAPI_PATH, media_type="application/yaml")
