from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.cache import CachedOrbitalElementsProvider
from app.core.errors import PropagationError, SourceUnavailableError
from app.providers.base import OrbitalElementsProvider, OrbitalElementsSnapshot
from app.services.models import PropagationResult
from app.services.nearest import NearestObjectAccumulator
from app.services.orbit_calculation import OrbitCalculationService
from app.services.propagation import Sgp4PropagationService


def propagated(positions: list[list[float]], velocities: list[list[float]]) -> PropagationResult:
    size = len(positions)
    return PropagationResult(
        positions_km=np.array(positions, dtype=np.float64),
        velocities_km_s=np.array(velocities, dtype=np.float64),
        valid=np.ones(size, dtype=np.bool_),
        error_codes=np.zeros(size, dtype=np.int64),
    )


def test_half_open_timeline_has_expected_seconds() -> None:
    start = datetime(2026, 9, 19, 10, tzinfo=UTC)
    timeline = OrbitCalculationService.build_timeline(start, start + timedelta(minutes=5))
    assert len(timeline) == 300
    assert timeline[0] == start
    assert timeline[-1] == start + timedelta(seconds=299)


def test_sgp4_returns_teme_position_and_velocity(elements) -> None:
    result = Sgp4PropagationService().propagate(
        elements["iss"],
        (elements["iss"].epoch,),
    )
    assert result.valid.tolist() == [True]
    assert result.positions_km.shape == (1, 3)
    assert result.velocities_km_s.shape == (1, 3)


def test_distance_uses_euclidean_norm(elements) -> None:
    iss = propagated([[0, 0, 0]], [[0, 0, 0]])
    candidate = propagated([[3, 4, 12]], [[0, 0, 0]])
    accumulator = NearestObjectAccumulator(iss)
    accumulator.consider(elements["candidate_a"], candidate)
    assert accumulator.distances_km[0] == pytest.approx(13.0)


def test_relative_speed_uses_velocity_vectors(elements) -> None:
    iss = propagated([[0, 0, 0]], [[1, 2, 3]])
    candidate = propagated([[1, 0, 0]], [[4, 6, 15]])
    accumulator = NearestObjectAccumulator(iss)
    accumulator.consider(elements["candidate_a"], candidate)
    assert accumulator.relative_speeds_km_s[0] == pytest.approx(13.0)


def test_nearest_object_is_selected(elements) -> None:
    iss = propagated([[0, 0, 0]], [[0, 0, 0]])
    accumulator = NearestObjectAccumulator(iss)
    accumulator.consider(elements["candidate_a"], propagated([[10, 0, 0]], [[0, 0, 0]]))
    accumulator.consider(elements["candidate_b"], propagated([[2, 0, 0]], [[0, 0, 0]]))
    assert accumulator.elements[0].norad_id == 23456
    assert accumulator.distances_km[0] == pytest.approx(2.0)


class StaticProvider(OrbitalElementsProvider):
    def __init__(self, snapshot=None, error: Exception | None = None) -> None:
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    async def get_snapshot(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshot


class StaticPropagation:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def propagate(self, _elements, times):
        if self.fail:
            raise RuntimeError("fixture propagation failure")
        size = len(times)
        return propagated([[0, 0, 0]] * size, [[0, 0, 0]] * size)


@pytest.mark.asyncio
async def test_empty_object_list_returns_no_candidates(elements) -> None:
    now = datetime(2026, 9, 19, tzinfo=UTC)
    snapshot = OrbitalElementsSnapshot((elements["iss"],), now, "Fixture")
    service = OrbitCalculationService(
        StaticProvider(snapshot),
        StaticPropagation(),
        max_elements_age_hours=168,
    )
    result = await service.calculate(now, now + timedelta(seconds=2))
    assert result.quality_status == "NO_CANDIDATES"
    assert all(item is None for item in result.nearest_elements)


@pytest.mark.asyncio
async def test_external_source_error_is_not_hidden(elements) -> None:
    provider = StaticProvider(error=SourceUnavailableError("offline"))
    service = OrbitCalculationService(
        provider,
        StaticPropagation(),
        max_elements_age_hours=168,
    )
    now = elements["iss"].epoch
    with pytest.raises(SourceUnavailableError, match="offline"):
        await service.calculate(now, now + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_iss_propagation_error_is_reported(elements) -> None:
    now = elements["iss"].epoch
    snapshot = OrbitalElementsSnapshot((elements["iss"],), now, "Fixture")
    service = OrbitCalculationService(
        StaticProvider(snapshot),
        StaticPropagation(fail=True),
        max_elements_age_hours=168,
    )
    with pytest.raises(PropagationError, match="ISS propagation failed"):
        await service.calculate(now, now + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_elements_snapshot_is_cached(elements) -> None:
    now = elements["iss"].epoch
    source = StaticProvider(OrbitalElementsSnapshot((elements["iss"],), now, "Fixture"))
    cached = CachedOrbitalElementsProvider(source, ttl_seconds=60)
    assert await cached.get_snapshot() is await cached.get_snapshot()
    assert source.calls == 1
