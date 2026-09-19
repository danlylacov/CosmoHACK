from __future__ import annotations

import numpy as np

from app.providers.base import OrbitalElements
from app.services.models import FloatArray, PropagationResult


class NearestObjectAccumulator:
    def __init__(self, iss: PropagationResult) -> None:
        sample_count = len(iss.valid)
        self._iss = iss
        self.distances_km = np.full(sample_count, np.nan, dtype=np.float64)
        self.relative_speeds_km_s = np.full(sample_count, np.nan, dtype=np.float64)
        self.positions_km = np.full((sample_count, 3), np.nan, dtype=np.float64)
        self.velocities_km_s = np.full((sample_count, 3), np.nan, dtype=np.float64)
        self.elements: list[OrbitalElements | None] = [None] * sample_count

    def consider(
        self,
        elements: OrbitalElements,
        candidate: PropagationResult,
    ) -> None:
        valid = self._iss.valid & candidate.valid
        position_delta = self._iss.positions_km - candidate.positions_km
        velocity_delta = self._iss.velocities_km_s - candidate.velocities_km_s
        distances = self.euclidean_norm(position_delta)
        relative_speeds = self.euclidean_norm(velocity_delta)
        better = valid & (np.isnan(self.distances_km) | (distances < self.distances_km))

        self.distances_km[better] = distances[better]
        self.relative_speeds_km_s[better] = relative_speeds[better]
        self.positions_km[better] = candidate.positions_km[better]
        self.velocities_km_s[better] = candidate.velocities_km_s[better]
        for index in np.flatnonzero(better):
            self.elements[int(index)] = elements

    @staticmethod
    def euclidean_norm(vectors: FloatArray) -> FloatArray:
        return np.sqrt(np.sum(vectors**2, axis=-1))
