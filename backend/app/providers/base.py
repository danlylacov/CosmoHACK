from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OrbitalElements:
    norad_id: int
    name: str
    object_type: str | None
    tle_line1: str
    tle_line2: str
    epoch: datetime
    retrieved_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class OrbitalElementsSnapshot:
    elements: tuple[OrbitalElements, ...]
    retrieved_at: datetime
    source: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


class OrbitalElementsProvider(ABC):
    @abstractmethod
    async def get_snapshot(self) -> OrbitalElementsSnapshot:
        """Return ISS and the current set of screened conjunction candidates."""
