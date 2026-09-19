from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.providers.base import OrbitalElements
from app.services.models import CalculationResult, PropagationResult

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "elements.json"


@pytest.fixture
def elements() -> dict[str, OrbitalElements]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    epoch = datetime(2026, 9, 19, tzinfo=UTC)
    retrieved_at = epoch
    return {
        key: OrbitalElements(
            **value,
            epoch=epoch,
            retrieved_at=retrieved_at,
            source="Fixture",
        )
        for key, value in payload.items()
    }


@pytest.fixture
def result_factory(
    elements: dict[str, OrbitalElements],
) -> Callable[[list[float | None], list[int | None] | None], CalculationResult]:
    def make_result(
        distances: list[float | None],
        ids: list[int | None] | None = None,
    ) -> CalculationResult:
        size = len(distances)
        start = datetime(2026, 9, 19, 10, tzinfo=UTC)
        ids = ids or [12345 if value is not None else None for value in distances]
        lookup = {
            12345: elements["candidate_a"],
            23456: elements["candidate_b"],
        }
        nearest = tuple(lookup.get(value) for value in ids)
        distance_array = np.array(
            [np.nan if value is None else value for value in distances],
            dtype=np.float64,
        )
        nearest_positions = np.zeros((size, 3), dtype=np.float64)
        nearest_positions[:, 0] = np.nan_to_num(distance_array)
        nearest_velocities = np.zeros((size, 3), dtype=np.float64)
        nearest_velocities[:, 0] = 10.0
        iss = PropagationResult(
            positions_km=np.zeros((size, 3), dtype=np.float64),
            velocities_km_s=np.zeros((size, 3), dtype=np.float64),
            valid=np.ones(size, dtype=np.bool_),
            error_codes=np.zeros(size, dtype=np.int64),
        )
        return CalculationResult(
            times=tuple(start + timedelta(seconds=index) for index in range(size)),
            iss_elements=elements["iss"],
            iss=iss,
            nearest_elements=nearest,
            nearest_positions_km=nearest_positions,
            nearest_velocities_km_s=nearest_velocities,
            distances_km=distance_array,
            relative_speeds_km_s=np.where(np.isnan(distance_array), np.nan, 10.0),
            source="Fixture",
            retrieved_at=start,
            elements_epoch=start,
            calculated_at=start,
            quality_status="COMPLETE"
            if all(value is not None for value in distances)
            else "PARTIAL",
            warnings=(),
        )

    return make_result
