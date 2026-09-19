from __future__ import annotations

from functools import lru_cache

from app.cache import CachedOrbitalElementsProvider
from app.core.config import Settings
from app.providers import CelesTrakOrbitalElementsProvider
from app.services.orbit_calculation import OrbitCalculationService
from app.services.propagation import Sgp4PropagationService


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache
def get_calculation_service() -> OrbitCalculationService:
    settings = get_settings()
    source = CelesTrakOrbitalElementsProvider(
        timeout_seconds=settings.source_timeout_seconds,
        max_records=settings.socrates_max_records,
    )
    provider = CachedOrbitalElementsProvider(
        source,
        ttl_seconds=settings.elements_cache_ttl_seconds,
    )
    return OrbitCalculationService(
        provider,
        Sgp4PropagationService(),
        max_elements_age_hours=settings.elements_max_age_hours,
    )
