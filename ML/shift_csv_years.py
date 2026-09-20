#!/usr/bin/env python3
"""Добавить копии строк: 2026 → 2024, 2025 → 2023, сохранив исходные.

Меняется только time_utc. Копии — синтетические данные, не реальные наблюдения
прошлых лет. CSV должен быть отсортирован по времени без дублей. При совпадении
времени исходная строка имеет приоритет. Повторный запуск не добавляет дублей.
JSON не изменяется.

Запуск: python3 shift_csv_years.py
Другой результат: python3 shift_csv_years.py --output /путь/results.csv
"""
import argparse
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import heapq
from itertools import groupby
import os
from pathlib import Path
import tempfile

from data.tools.update_dataset.update_dataset import dataset_lock

DEFAULT = Path(__file__).resolve().parent / 'data/datasets/results.csv'
YEARS = {2026: 2024, 2025: 2023}


def rows(source, header, shifted):
    column = header.index('time_utc')
    previous = None
    with source.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader, None) != header:
            raise ValueError('Заголовок CSV изменился во время чтения')
        for row in reader:
            if len(row) != len(header):
                raise ValueError(f'Строка {reader.line_num}: число полей не совпадает с заголовком')
            time = datetime.fromisoformat(row[column].strip().replace('Z', '+00:00'))
            time = time.replace(tzinfo=time.tzinfo or timezone.utc).astimezone(timezone.utc)
            if shifted:
                if time.year not in YEARS:
                    continue
                time = time.replace(year=YEARS[time.year])
            if previous is not None and time <= previous:
                raise ValueError('CSV должен быть отсортирован по time_utc без дублей')
            previous = time
            row[column] = time.isoformat().replace('+00:00', 'Z')
            yield time, 0 if shifted else 1, row


def augment(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as locks:
        for directory in sorted({source.parent, output.parent}):
            locks.enter_context(dataset_lock(directory))
        with source.open(encoding='utf-8-sig', newline='') as stream:
            header = next(csv.reader(stream, strict=True), [])
        if 'time_utc' not in header or len(set(header)) != len(header) or '' in header:
            raise ValueError('Нужен уникальный заголовок с колонкой time_utc')
        fd, temporary = tempfile.mkstemp(prefix='.shift-years-', suffix='.csv', dir=output.parent)
        count = 0
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                ordered = heapq.merge(rows(source, header, True), rows(source, header, False))
                for _, group in groupby(ordered, key=lambda item: item[0]):
                    for _, _, selected in group:
                        pass  # Исходная строка идёт последней и имеет приоритет.
                    writer.writerow(selected)
                    count += 1
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', type=Path, default=DEFAULT, help='исходный CSV (по умолчанию data/datasets/results.csv)')
    parser.add_argument('--output', type=Path, help='куда записать результат (по умолчанию заменить исходный CSV)')
    args = parser.parse_args()
    output = args.output or args.input
    try:
        count = augment(args.input, output)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f'Ошибка: {error}\n')
    print(f'{output.resolve()}: {count} строк, исходные данные + копии со сдвигом годов.')
