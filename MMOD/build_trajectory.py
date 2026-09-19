#!/usr/bin/env python3
"""
Построение траекторий МКС и объектов из SOCRATES на N часов вперёд.

TLE: CelesTrak (по NORAD ID). Координаты: SGP4 → TEME (x, y, z), км.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
from sgp4.api import jday
from skyfield.api import EarthSatellite

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from get_data import fetch_iss_conjunctions

ISS_NORAD = "25544"
CELESTRAK_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php"
USER_AGENT = "Mozilla/5.0 (compatible; ISS-Conjunction-Fetcher/1.0)"
OUTPUT_PATH = _SCRIPT_DIR / "trajectories.json"
TLE_TIMEOUT = 30.0
MAX_CONCURRENT_TLE = 5
COORDINATE_FRAME = "TEME"
POSITION_UNIT = "km"


def _object_id(norad_id: int) -> str:
    if str(norad_id) == ISS_NORAD:
        return f"ISS-{norad_id}"
    return f"DEBRIS-{norad_id}"


def _parse_tle(text: str, norad_id: str) -> EarthSatellite:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 3 or not lines[1].startswith("1 ") or not lines[2].startswith("2 "):
        raise ValueError(f"Не удалось получить TLE для NORAD {norad_id}")
    name, line1, line2 = lines[0], lines[1], lines[2]
    return EarthSatellite(line1, line2, name)


async def fetch_tle(
    client: httpx.AsyncClient,
    norad_id: str,
    cache: dict[str, EarthSatellite],
    lock: asyncio.Lock,
    sem: asyncio.Semaphore,
) -> EarthSatellite:
    """Скачивает TLE с CelesTrak и возвращает объект EarthSatellite."""
    norad_id = str(norad_id).strip()
    async with lock:
        cached = cache.get(norad_id)
    if cached is not None:
        return cached

    async with sem:
        resp = await client.get(
            CELESTRAK_TLE_URL,
            params={"CATNR": norad_id, "FORMAT": "tle"},
        )
        resp.raise_for_status()
        sat = _parse_tle(resp.text, norad_id)

    async with lock:
        cache[norad_id] = sat
    print(f"✅ TLE получен: {sat.name.strip()} (NORAD {norad_id})")
    return sat


def build_trajectory(
    sat: EarthSatellite,
    norad_id: int,
    t0: datetime,
    hours: float,
    step_sec: int,
) -> list[dict]:
    """Считает TEME-позиции на интервале [t0, t0 + hours]."""
    total_seconds = int(hours * 3600)
    datetimes = [
        t0 + timedelta(seconds=s) for s in range(0, total_seconds + 1, step_sec)
    ]

    jd = np.empty(len(datetimes))
    fr = np.empty(len(datetimes))
    for i, dt in enumerate(datetimes):
        jd[i], fr[i] = jday(
            dt.year,
            dt.month,
            dt.day,
            dt.hour,
            dt.minute,
            dt.second + dt.microsecond * 1e-6,
        )

    errors, positions, _velocities = sat.model.sgp4_array(jd, fr)
    object_id = _object_id(norad_id)

    records = []
    for dt, error, xyz in zip(datetimes, errors, positions):
        if int(error) != 0:
            continue
        records.append(
            {
                "object_id": object_id,
                "norad_id": norad_id,
                "time_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "coordinate_frame": COORDINATE_FRAME,
                "position_unit": POSITION_UNIT,
                "position": {
                    "x": round(float(xyz[0]), 3),
                    "y": round(float(xyz[1]), 3),
                    "z": round(float(xyz[2]), 3),
                },
            }
        )
    return records


def _unique_targets(events: list[dict]) -> list[str]:
    norads = [ISS_NORAD]
    seen = {ISS_NORAD}
    for event in events:
        norad = str(event.get("other_norad", "")).strip()
        if not norad or norad in seen:
            continue
        seen.add(norad)
        norads.append(norad)
    return norads


async def _build_one(
    client: httpx.AsyncClient,
    norad: str,
    t0: datetime,
    hours: float,
    step_sec: int,
    cache: dict[str, EarthSatellite],
    lock: asyncio.Lock,
    sem: asyncio.Semaphore,
) -> list[dict]:
    try:
        sat = await fetch_tle(client, norad, cache, lock, sem)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"⚠️  Пропуск NORAD {norad}: {exc}")
        return []

    return await asyncio.to_thread(
        build_trajectory, sat, int(norad), t0, hours, step_sec
    )


async def build_trajectories(
    hours: float,
    step_sec: int = 60,
    events: list[dict] | None = None,
) -> list[dict]:
    """
    Строит траектории МКС и каждого уникального объекта из SOCRATES.

    Окно: now → now + hours (UTC).
    Возвращает плоский список точек в TEME.
    """
    if hours <= 0:
        raise ValueError("hours должен быть положительным")
    if step_sec <= 0:
        raise ValueError("step_sec должен быть положительным")

    t0 = datetime.now(timezone.utc)

    if events is None:
        events = await asyncio.to_thread(fetch_iss_conjunctions)

    cache: dict[str, EarthSatellite] = {}
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(MAX_CONCURRENT_TLE)
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(headers=headers, timeout=TLE_TIMEOUT) as client:
        tasks = [
            _build_one(client, norad, t0, hours, step_sec, cache, lock, sem)
            for norad in _unique_targets(events)
        ]
        batches = await asyncio.gather(*tasks)

    return [record for batch in batches for record in batch]


def _print_summary(records: list[dict]) -> None:
    counts = Counter((r["object_id"], r["norad_id"]) for r in records)
    print(f"\nТочек: {len(records)}, объектов: {len(counts)}")
    print(f"{'object_id':<16} {'norad_id':<10} {'точек'}")
    print("-" * 40)
    for (object_id, norad_id), n in counts.items():
        print(f"{object_id:<16} {norad_id:<10} {n}")


async def main() -> None:
    hours = 24
    step_sec = 60
    output_path = OUTPUT_PATH

    records = await build_trajectories(hours=hours, step_sec=step_sec)
    _print_summary(records)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n💾 Результат сохранён: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
