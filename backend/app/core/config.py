from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    elements_cache_ttl_seconds: int = 3600
    elements_max_age_hours: int = 168
    source_timeout_seconds: float = 30.0
    socrates_max_records: int = 100
    gzip_minimum_size: int = 1000

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            elements_cache_ttl_seconds=int(os.getenv("ELEMENTS_CACHE_TTL_SECONDS", "3600")),
            elements_max_age_hours=int(os.getenv("ELEMENTS_MAX_AGE_HOURS", "168")),
            source_timeout_seconds=float(os.getenv("SOURCE_TIMEOUT_SECONDS", "30")),
            socrates_max_records=int(os.getenv("SOCRATES_MAX_RECORDS", "100")),
            gzip_minimum_size=int(os.getenv("GZIP_MINIMUM_SIZE", "1000")),
        )
