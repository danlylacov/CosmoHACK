from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic

from app.core.errors import SourceUnavailableError
from app.providers.base import OrbitalElementsProvider, OrbitalElementsSnapshot


class CachedOrbitalElementsProvider(OrbitalElementsProvider):
    def __init__(self, provider: OrbitalElementsProvider, *, ttl_seconds: int) -> None:
        self._provider = provider
        self._ttl_seconds = ttl_seconds
        self._snapshot: OrbitalElementsSnapshot | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_snapshot(self) -> OrbitalElementsSnapshot:
        if self._snapshot is not None and monotonic() < self._expires_at:
            return self._snapshot

        async with self._lock:
            if self._snapshot is not None and monotonic() < self._expires_at:
                return self._snapshot
            try:
                snapshot = await self._provider.get_snapshot()
            except SourceUnavailableError:
                if self._snapshot is None:
                    raise
                return replace(
                    self._snapshot,
                    warnings=(
                        *self._snapshot.warnings,
                        "CelesTrak refresh failed; stale cached snapshot was used",
                    ),
                )
            self._snapshot = snapshot
            self._expires_at = monotonic() + self._ttl_seconds
            return snapshot
