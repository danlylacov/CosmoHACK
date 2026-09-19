"""Stream observations JSON into an accumulated dataset, replacing fresh CSV windows.

Only one sample and the small series metadata are decoded at once. Original series
remain separate: series_to_csv gives each series equal weight. JSON keeps observations;
CSV remains authoritative for empty cells when the selected metric set changes.
"""
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

STEP = timedelta(minutes=30)
INTERVAL_SECONDS = {"geomagnetic_kp": 10800, "geomagnetic_ap": 10800}


def utc(value):
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return time.replace(tzinfo=time.tzinfo or timezone.utc).astimezone(timezone.utc)


def stamp(time):
    return time.isoformat().replace("+00:00", "Z")


def floor(time):
    return time.replace(minute=time.minute // 30 * 30, second=0, microsecond=0)


def ceil(time):
    rounded = floor(time)
    return rounded if rounded == time else rounded + STEP


def _reject_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


class _Reader:
    """Incremental JSON decoder; arrays of samples are traversed, never materialized."""
    def __init__(self, stream):
        self.stream, self.buffer, self.position, self.eof = stream, "", 0, False
        self.decoder = json.JSONDecoder(parse_constant=_reject_constant)

    def refill(self):
        self.buffer = self.buffer[self.position:]
        self.position = 0
        part = self.stream.read(65536)
        self.buffer += part
        self.eof = not part

    def peek(self):
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position].isspace():
                self.position += 1
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if self.eof:
                return ""
            self.refill()

    def expect(self, token):
        if self.peek() != token:
            raise ValueError(f"Invalid JSON: expected {token!r}, got {self.peek()!r}")
        self.position += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
                # A scalar at the chunk edge may continue in the next chunk.
                if end < len(self.buffer) or self.eof:
                    self.position = end
                    return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("Invalid or truncated JSON") from None
            if len(self.buffer) - self.position > 8 * 1024 * 1024:
                raise ValueError("One JSON value exceeds the 8 MiB safety limit")
            self.refill()

    def keys(self):
        self.expect("{")
        seen = set()
        if self.peek() != "}":
            while True:
                key = self.value()
                if not isinstance(key, str):
                    raise ValueError("JSON object key must be a string")
                if key in seen:
                    raise ValueError(f"Duplicate JSON object key: {key}")
                seen.add(key)
                self.expect(":")
                yield key
                if self.peek() != ",":
                    break
                self.expect(",")
        self.expect("}")

    def elements(self):
        self.expect("[")
        if self.peek() != "]":
            while True:
                yield None
                if self.peek() != ",":
                    break
                self.expect(",")
        self.expect("]")

    def skip(self):
        if self.peek() == "{":
            for _ in self.keys():
                self.skip()
        elif self.peek() == "[":
            for _ in self.elements():
                self.skip()
        else:
            self.value()


def read_period(path):
    """Read just period, normally at the start of the file."""
    with Path(path).open(encoding="utf-8-sig") as stream:
        reader = _Reader(stream)
        for key in reader.keys():
            if key == "period":
                period = reader.value()
                if not isinstance(period, dict):
                    raise ValueError("period must be an object")
                return period
            reader.skip()
    raise ValueError(f"{path}: missing period")


def iter_series(path):
    """Yield (metadata, samples iterator); consume samples before requesting next series.

    Metadata is complete once samples is exhausted. Unusual input with metric after
    samples uses a temporary disk spool; generated files use metric first.
    """
    with Path(path).open(encoding="utf-8-sig") as stream:
        reader, found = _Reader(stream), False
        for key in reader.keys():
            if key != "series":
                reader.skip()
                continue
            if found:
                raise ValueError("Duplicate series key")
            found = True
            for _ in reader.elements():
                metadata = {}
                keys = reader.keys()
                for field in keys:
                    if field == "samples":
                        break
                    metadata[field] = reader.value()
                else:
                    raise ValueError("Series has no samples")

                def samples():
                    for _ in reader.elements():
                        sample = reader.value()
                        if not isinstance(sample, dict):
                            raise ValueError("Sample must be an object")
                        yield sample
                    for field in keys:
                        if field == "samples":
                            raise ValueError("Duplicate samples key")
                        metadata[field] = reader.value()

                if "metric" in metadata:
                    yield metadata, samples()
                else:
                    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as spool:
                        for sample in samples():
                            spool.write(json.dumps(sample, ensure_ascii=False) + "\n")
                        spool.seek(0)
                        yield metadata, (json.loads(line) for line in spool)
        if not found or reader.peek():
            raise ValueError("Missing series or trailing JSON content")


def _union(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _period_ranges(period):
    start, end = utc(period["start_utc"]), utc(period["available_until_exclusive_utc"])
    if end <= start:
        raise ValueError("Period end must be after start")
    ranges = []
    for item in period.get("included_ranges_utc", [{"start_utc": stamp(start), "end_exclusive_utc": stamp(end)}]):
        left, right = utc(item["start_utc"]), utc(item["end_exclusive_utc"])
        if right <= left:
            raise ValueError("Included range end must be after start")
        left, right = max(floor(left), floor(start)), min(ceil(right), ceil(end))
        if left < right:
            ranges.append((left, right))
    return start, end, _union(ranges)


def _subtract(ranges, replaced):
    result = []
    for left, right in ranges:
        for start, end in replaced:
            if end <= left or start >= right:
                continue
            if left < start:
                result.append((left, start))
            left = max(left, end)
            if left >= right:
                break
        if left < right:
            result.append((left, right))
    return result


def _samples(samples, metric, start, end, ranges):
    for source in samples:
        time, value = utc(source["time_utc"]), source["value"]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"{metric}: value must be a finite number or null")
        quality = source.get("quality")
        quality = dict(quality) if isinstance(quality, dict) else {}
        seconds = quality.get("interval_seconds", INTERVAL_SECONDS.get(metric, 0))
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"{metric}: invalid interval_seconds")
        stop = utc(quality["interval_end_utc"]) if quality.get("interval_end_utc") else time + timedelta(seconds=seconds)
        duration = (stop - time).total_seconds()
        if duration < 0:
            raise ValueError(f"{metric}: interval ends before sample")
        sample = dict(source, time_utc=stamp(time))
        if quality.get("interval_end_utc"):
            quality["interval_end_utc"] = stamp(stop)
            sample["quality"] = quality
        if duration <= STEP.total_seconds():
            if start <= time < end and any(left <= floor(time) < right for left, right in ranges):
                yield sample
            continue
        # Split long observations around complete replaced CSV windows. Preserve
        # their original cadence so a short fragment never acquires fast stats.
        for left, right in ranges:
            left, right = max(time, start, left), min(stop, end, right)
            if left >= right:
                continue
            if left == time and right == stop:
                yield sample
            else:
                interval = dict(quality, interval_seconds=(right-left).total_seconds(),
                                interval_end_utc=stamp(right),
                                original_interval_seconds=quality.get("original_interval_seconds", duration))
                yield dict(sample, time_utc=stamp(left), quality=interval)


def merge_json(old, new, output):
    """Merge observations atomically; fresh 30-minute rows replace all old metrics.

    old=None initializes a dataset. Files can be arbitrarily large; memory use is
    bounded by one sample/metadata value and the list of disjoint date ranges.
    Returns counts and the resulting period. Output may equal either input path.
    """
    sources = []
    for path in ([old] if old is not None else []) + [new]:
        start, end, ranges = _period_ranges(read_period(path))
        sources.append((Path(path), start, end, ranges))
    fresh_ranges = sources[-1][3]
    included = _union([r for _, _, _, ranges in sources for r in ranges])
    period = {"start_utc": stamp(min(s[1] for s in sources)),
              "available_until_exclusive_utc": stamp(max(s[2] for s in sources)),
              "included_ranges_utc": [{"start_utc": stamp(a), "end_exclusive_utc": stamp(b)} for a, b in included]}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    stats = {"series": 0, "samples": 0, "period": period,
             "csv_semantics_note": "CSV is authoritative for empty cells: when metrics change, "
             "reconverting accumulated JSON can produce count=0 where appended CSV has a blank."}
    empty_metrics, populated_metrics = set(), set()
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write('{"period":' + json.dumps(period, separators=(",", ":")) + ',"series":[')
            for index, (path, start, end, ranges) in enumerate(sources):
                retained = _subtract(ranges, fresh_ranges) if index < len(sources)-1 else ranges
                for metadata, samples in iter_series(path):
                    metric = metadata.get("metric")
                    if not isinstance(metric, str) or not metric:
                        raise ValueError(f"{path}: missing metric")
                    selected = _samples(samples, metric, start, end, retained)
                    first = next(selected, None)
                    if first is None:
                        # Empty series affect only the column set, never series
                        # weighting. Retain at most one empty entry per metric.
                        if metric in empty_metrics or metric in populated_metrics:
                            continue
                        empty_metrics.add(metric)
                    else:
                        populated_metrics.add(metric)
                    if stats["series"]:
                        stream.write(",")
                    stream.write('{"metric":' + json.dumps(metric, ensure_ascii=False) + ',"samples":[')
                    count = int(first is not None)
                    if first is not None:
                        stream.write(json.dumps(first, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
                    for sample in selected:
                        stream.write(",")
                        stream.write(json.dumps(sample, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
                        count += 1
                    stream.write("]")
                    for field, value in metadata.items():
                        if field != "metric":
                            stream.write("," + json.dumps(field) + ":" + json.dumps(value, ensure_ascii=False, allow_nan=False))
                    stream.write("}")
                    stats["series"] += 1
                    stats["samples"] += count
            stream.write("]}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return stats
