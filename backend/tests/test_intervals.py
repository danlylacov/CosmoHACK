from datetime import timedelta

from app.services.critical_intervals import CriticalIntervalService


def test_one_critical_interval(result_factory) -> None:
    result = result_factory([6.0, 4.0, 2.0, 4.0, 6.0])
    summary, intervals = CriticalIntervalService().build(
        result, 5.0, result.times[-1] + timedelta(seconds=1)
    )
    assert len(intervals) == 1
    assert intervals[0].start_time == result.times[1]
    assert intervals[0].end_time == result.times[4]
    assert intervals[0].tca == result.times[2]
    assert summary.critical_duration_seconds == 3


def test_multiple_critical_intervals(result_factory) -> None:
    result = result_factory([2.0, 6.0, 3.0, 6.0, 4.0])
    _, intervals = CriticalIntervalService().build(
        result, 5.0, result.times[-1] + timedelta(seconds=1)
    )
    assert len(intervals) == 3
    assert [item.minimum_distance_km for item in intervals] == [2.0, 3.0, 4.0]


def test_nearest_object_change_splits_interval(result_factory) -> None:
    result = result_factory([2.0, 2.5, 3.0], [12345, 23456, 23456])
    _, intervals = CriticalIntervalService().build(
        result, 5.0, result.times[-1] + timedelta(seconds=1)
    )
    assert len(intervals) == 2
    assert intervals[0].end_time == result.times[1]
    assert intervals[0].object.norad_id == 12345
    assert intervals[1].start_time == result.times[1]
    assert intervals[1].object.norad_id == 23456


def test_distance_equal_to_threshold_is_not_critical(result_factory) -> None:
    result = result_factory([4.0, 5.0])
    _, intervals = CriticalIntervalService().build(
        result, 5.0, result.times[-1] + timedelta(seconds=1)
    )
    assert len(intervals) == 1
    assert intervals[0].end_time == result.times[1]
