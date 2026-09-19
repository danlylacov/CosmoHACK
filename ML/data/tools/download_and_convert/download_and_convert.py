#!/usr/bin/env python3
"""Run the existing downloader, then atomically convert its fresh JSON to CSV."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DOWNLOADER = ROOT / 'space_weather_downloader'
CONVERTER = ROOT / 'space_weather_model_data' / 'series_to_csv.py'


def fingerprint(path):
    try:
        info = path.stat()
        return info.st_ino, info.st_size, info.st_mtime_ns
    except FileNotFoundError:
        return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Only inspect output/mode flags. The original CLI validates every argument.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--out', type=Path, default=DOWNLOADER)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('-h', '--help', action='store_true')
    options, _ = parser.parse_known_args(argv)
    if not CONVERTER.is_file():
        print(f'Конвертер не найден: {CONVERTER}', file=sys.stderr)
        return 1
    json_path = options.out.resolve() / 'results.json'
    before = fingerprint(json_path)
    downloaded = subprocess.run([str(DOWNLOADER / 'run.sh'), *argv])
    code = downloaded.returncode
    if options.help or options.plan_only:
        return code
    # Exit 2 can mean either argparse failure or a newly saved partial result.
    # Never silently convert a previous run after a failed download.
    after = fingerprint(json_path)
    if code not in (0, 2) or after is None or after == before:
        if code == 0:
            print('Загрузчик не создал новый results.json; CSV не изменён.', file=sys.stderr)
        return code or 1
    csv_path = json_path.with_suffix('.csv')
    fd, temporary = tempfile.mkstemp(prefix='.results-', suffix='.csv', dir=csv_path.parent)
    os.close(fd)
    try:
        converted = subprocess.run([str(DOWNLOADER / '.venv' / 'bin' / 'python'), '-B',
                                    str(CONVERTER), str(json_path), temporary])
        if converted.returncode:
            print('Ошибка конвертации; предыдущий CSV сохранён.', file=sys.stderr)
            return converted.returncode
        os.replace(temporary, csv_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f'CSV: {csv_path}', flush=True)
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except OSError as exc:
        print(f'Не удалось выполнить выгрузку/конвертацию: {exc}', file=sys.stderr)
        raise SystemExit(1)
