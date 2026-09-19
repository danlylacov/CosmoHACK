from __future__ import annotations

from datetime import datetime

import numpy as np
from sgp4.api import Satrec, jday

from app.core.errors import OrbitalElementsError
from app.providers.base import OrbitalElements
from app.services.models import PropagationResult


class Sgp4PropagationService:
    def propagate(
        self,
        elements: OrbitalElements,
        times: tuple[datetime, ...],
    ) -> PropagationResult:
        try:
            satellite = Satrec.twoline2rv(elements.tle_line1, elements.tle_line2)
        except (TypeError, ValueError) as exc:
            raise OrbitalElementsError(f"Invalid TLE for NORAD {elements.norad_id}: {exc}") from exc

        seconds = np.fromiter(
            (value.second + value.microsecond / 1_000_000 for value in times),
            dtype=np.float64,
            count=len(times),
        )
        jd, fraction = jday(
            np.fromiter((value.year for value in times), dtype=np.int64, count=len(times)),
            np.fromiter((value.month for value in times), dtype=np.int64, count=len(times)),
            np.fromiter((value.day for value in times), dtype=np.int64, count=len(times)),
            np.fromiter((value.hour for value in times), dtype=np.int64, count=len(times)),
            np.fromiter((value.minute for value in times), dtype=np.int64, count=len(times)),
            seconds,
        )
        error_codes, positions, velocities = satellite.sgp4_array(
            np.asarray(jd, dtype=np.float64),
            np.asarray(fraction, dtype=np.float64),
        )
        error_codes = np.asarray(error_codes, dtype=np.int64)
        positions = np.asarray(positions, dtype=np.float64)
        velocities = np.asarray(velocities, dtype=np.float64)
        valid = (
            (error_codes == 0)
            & np.isfinite(positions).all(axis=1)
            & np.isfinite(velocities).all(axis=1)
        )
        return PropagationResult(
            positions_km=positions,
            velocities_km_s=velocities,
            valid=valid,
            error_codes=error_codes,
        )
