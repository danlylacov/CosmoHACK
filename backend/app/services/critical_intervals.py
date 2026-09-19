from __future__ import annotations

from datetime import datetime

import numpy as np

from app.schemas.models import (
    CriticalInterval,
    DistanceSummary,
    ObjectIdentity,
    SummaryObject,
)
from app.services.models import CalculationResult


class CriticalIntervalService:
    def build(
        self,
        result: CalculationResult,
        threshold_km: float | None,
        request_end_time: datetime,
    ) -> tuple[DistanceSummary, list[CriticalInterval]]:
        intervals = (
            self._build_intervals(result, threshold_km, request_end_time)
            if threshold_km is not None
            else []
        )
        valid_indices = np.flatnonzero(np.isfinite(result.distances_km))
        if len(valid_indices) == 0:
            summary = DistanceSummary(
                minimum_distance_km=None,
                tca=None,
                nearest_object=None,
                critical_duration_seconds=0,
            )
            return summary, intervals

        global_index = int(valid_indices[np.argmin(result.distances_km[valid_indices])])
        elements = result.nearest_elements[global_index]
        summary_object = (
            SummaryObject(norad_id=elements.norad_id, name=elements.name)
            if elements is not None
            else None
        )
        duration = sum(
            int((interval.end_time - interval.start_time).total_seconds()) for interval in intervals
        )
        return (
            DistanceSummary(
                minimum_distance_km=float(result.distances_km[global_index]),
                tca=result.times[global_index],
                nearest_object=summary_object,
                critical_duration_seconds=duration,
            ),
            intervals,
        )

    def _build_intervals(
        self,
        result: CalculationResult,
        threshold_km: float,
        request_end_time: datetime,
    ) -> list[CriticalInterval]:
        intervals: list[CriticalInterval] = []
        start: int | None = None
        current_norad: int | None = None

        for index, (distance, elements) in enumerate(
            zip(result.distances_km, result.nearest_elements, strict=True)
        ):
            is_critical = (
                elements is not None and np.isfinite(distance) and float(distance) < threshold_km
            )
            norad_id = elements.norad_id if elements is not None else None
            if start is not None and (not is_critical or norad_id != current_norad):
                intervals.append(self._make_interval(result, start, index))
                start = None
                current_norad = None
            if is_critical and start is None:
                start = index
                current_norad = norad_id

        if start is not None:
            intervals.append(
                self._make_interval(result, start, len(result.times), request_end_time)
            )
        return intervals

    @staticmethod
    def _make_interval(
        result: CalculationResult,
        start: int,
        stop: int,
        end_time: datetime | None = None,
    ) -> CriticalInterval:
        indices = np.arange(start, stop)
        tca_index = int(indices[np.argmin(result.distances_km[indices])])
        elements = result.nearest_elements[start]
        assert elements is not None
        return CriticalInterval(
            start_time=result.times[start],
            end_time=end_time if end_time is not None else result.times[stop],
            tca=result.times[tca_index],
            minimum_distance_km=float(result.distances_km[tca_index]),
            maximum_relative_speed_km_s=float(np.max(result.relative_speeds_km_s[indices])),
            object=ObjectIdentity(
                norad_id=elements.norad_id,
                name=elements.name,
                object_type=elements.object_type,
            ),
        )
