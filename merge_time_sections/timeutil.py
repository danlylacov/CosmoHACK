"""UTC timestamps and interval checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MAX_WINDOW = timedelta(hours=24)


class IntervalError(ValueError):
    """Requested window is empty, inverted, or longer than 24 hours."""


def parse_utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def validate_window(start: datetime, end: datetime) -> None:
    if start >= end:
        raise IntervalError("start must be earlier than end")
    if end - start > MAX_WINDOW:
        raise IntervalError("window must be at most 24 hours")
