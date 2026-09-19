from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta

import numpy as np

from app.core.errors import PropagationError, StaleElementsError
from app.providers.base import OrbitalElements, OrbitalElementsProvider
from app.services.models import CalculationResult
from app.services.nearest import NearestObjectAccumulator
from app.services.propagation import Sgp4PropagationService

ISS_NORAD_ID = 25544


class OrbitCalculationService:
    def __init__(
        self,
        provider: OrbitalElementsProvider,
        propagation: Sgp4PropagationService,
        *,
        max_elements_age_hours: int,
    ) -> None:
        self._provider = provider
        self._propagation = propagation
        self._max_elements_age = timedelta(hours=max_elements_age_hours)

    async def calculate(self, start_time: datetime, end_time: datetime) -> CalculationResult:
        # #region agent log
        _t0 = __import__("time").monotonic()
        try:
            import json as _json, time as _time
            with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"B","location":"orbit_calculation.py:calculate:start","message":"calculate started","data":{"start":start_time.isoformat(),"end":end_time.isoformat(),"window_s":(end_time-start_time).total_seconds()},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        snapshot = await self._provider.get_snapshot()
        # #region agent log
        try:
            import json as _json, time as _time
            with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"A","location":"orbit_calculation.py:calculate:snapshot","message":"snapshot ready","data":{"n_elements":len(snapshot.elements),"elapsed_s":round(__import__("time").monotonic()-_t0,3)},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        by_id = {item.norad_id: item for item in snapshot.elements}
        iss_elements = by_id.get(ISS_NORAD_ID)
        if iss_elements is None:
            raise PropagationError("ISS orbital elements are unavailable")
        if self._is_stale(iss_elements, start_time, end_time):
            raise StaleElementsError(
                "ISS orbital elements are too far from the requested period",
                details={"elements_epoch": iss_elements.epoch.isoformat()},
            )

        times = self.build_timeline(start_time, end_time)
        # #region agent log
        _t_prop = __import__("time").monotonic()
        try:
            import json as _json, time as _time
            with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"D","location":"orbit_calculation.py:calculate:timeline","message":"timeline built","data":{"n_times":len(times),"elapsed_s":round(__import__("time").monotonic()-_t0,3)},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        try:
            iss = await asyncio.to_thread(self._propagation.propagate, iss_elements, times)
        except Exception as exc:
            raise PropagationError(f"ISS propagation failed: {exc}") from exc
        if not iss.valid.all():
            failed = int((~iss.valid).sum())
            raise PropagationError(
                "ISS propagation failed for part of the requested period",
                details={"failed_samples": failed},
            )

        accumulator = NearestObjectAccumulator(iss)
        warnings = list(snapshot.warnings)
        used_epochs = [iss_elements.epoch]
        # #region agent log
        try:
            import json as _json, time as _time
            with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"B","location":"orbit_calculation.py:calculate:iss","message":"ISS propagated","data":{"n_times":len(times),"iss_elapsed_s":round(__import__("time").monotonic()-_t_prop,3)},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        _n_considered = 0
        for elements in snapshot.elements:
            if elements.norad_id == ISS_NORAD_ID:
                continue
            if self._is_stale(elements, start_time, end_time):
                warnings.append(f"NORAD {elements.norad_id} skipped because its elements are stale")
                continue
            try:
                propagated = await asyncio.to_thread(self._propagation.propagate, elements, times)
            except Exception as exc:
                warnings.append(f"NORAD {elements.norad_id} propagation failed: {exc}")
                continue
            if not propagated.valid.any():
                warnings.append(f"NORAD {elements.norad_id} has no valid propagated samples")
                continue
            if not propagated.valid.all():
                warnings.append(
                    f"NORAD {elements.norad_id} has "
                    f"{int((~propagated.valid).sum())} invalid samples"
                )
            accumulator.consider(elements, propagated)
            used_epochs.append(elements.epoch)
            _n_considered += 1

        # #region agent log
        try:
            import json as _json, time as _time
            with open("/Users/daniil/PycharmProjects/CosmoHACK/.cursor/debug-9c32b6.log", "a") as _f:
                _f.write(_json.dumps({"sessionId":"9c32b6","hypothesisId":"B","location":"orbit_calculation.py:calculate:candidates","message":"all candidates processed","data":{"n_elements":len(snapshot.elements),"n_considered":_n_considered,"prop_elapsed_s":round(__import__("time").monotonic()-_t_prop,3),"total_elapsed_s":round(__import__("time").monotonic()-_t0,3)},"timestamp":int(_time.time()*1000)})+"\n")
        except Exception:
            pass
        # #endregion
        missing = np.isnan(accumulator.distances_km)
        if missing.all():
            quality_status = "NO_CANDIDATES"
            warnings.append("No valid SOCRATES candidate was available")
        elif missing.any() or warnings:
            quality_status = "PARTIAL"
            if missing.any():
                warnings.append(f"No nearest object for {int(missing.sum())} samples")
        else:
            quality_status = "COMPLETE"

        return CalculationResult(
            times=times,
            iss_elements=iss_elements,
            iss=iss,
            nearest_elements=tuple(accumulator.elements),
            nearest_positions_km=accumulator.positions_km,
            nearest_velocities_km_s=accumulator.velocities_km_s,
            distances_km=accumulator.distances_km,
            relative_speeds_km_s=accumulator.relative_speeds_km_s,
            source=snapshot.source,
            retrieved_at=snapshot.retrieved_at,
            elements_epoch=min(used_epochs),
            calculated_at=datetime.now(UTC),
            quality_status=quality_status,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def build_timeline(start_time: datetime, end_time: datetime) -> tuple[datetime, ...]:
        count = math.ceil((end_time - start_time).total_seconds())
        return tuple(start_time + timedelta(seconds=index) for index in range(count))

    def _is_stale(
        self,
        elements: OrbitalElements,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        return max(abs(start_time - elements.epoch), abs(end_time - elements.epoch)) > (
            self._max_elements_age
        )
