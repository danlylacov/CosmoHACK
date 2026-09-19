#!/usr/bin/env python3
"""Объединить старый и свежий CSV по времени; при совпадении побеждает свежая строка."""
import argparse
import csv
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def merge_csv(old, new, output, time_column="time_utc"):
    fields, rows = [], {}
    for path in (Path(old), Path(new)):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            header = reader.fieldnames or []
            if time_column not in header or len(set(header)) != len(header) or "" in header:
                raise ValueError(f"{path}: нужен уникальный заголовок с колонкой {time_column}")
            fields.extend(field for field in header if field not in fields)
            for row in reader:
                if None in row or None in row.values():
                    raise ValueError(f"{path}, строка {reader.line_num}: число полей не совпадает с заголовком")
                try:
                    time = datetime.fromisoformat(row[time_column].strip().replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(f"{path}, строка {reader.line_num}: некорректное время {row[time_column]!r}") from None
                time = time.replace(tzinfo=time.tzinfo or timezone.utc).astimezone(timezone.utc)
                row[time_column] = time.isoformat().replace("+00:00", "Z")
                rows[time] = row

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows[time] for time in sorted(rows))
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="накопленная база или более старый CSV")
    parser.add_argument("new", type=Path, help="свежий CSV")
    parser.add_argument("output", type=Path, nargs="?", help="выходной CSV; по умолчанию обновляется первый файл")
    parser.add_argument("--time-column", default="time_utc", help="колонка времени (по умолчанию time_utc)")
    args = parser.parse_args()
    output = args.output or args.old
    try:
        count = merge_csv(args.old, args.new, output, args.time_column)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"Ошибка: {error}\n")
    print(f"{output}: {count} строк")
