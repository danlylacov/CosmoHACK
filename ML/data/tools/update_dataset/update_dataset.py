#!/usr/bin/env python3
"""Download, convert and merge a period into results.csv and results.json."""
import argparse
from contextlib import contextmanager
import csv
import fcntl
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

TOOLS = Path(__file__).resolve().parent.parent
DATASETS = TOOLS.parent / 'datasets'


def current_files(root):
    paths = root/'results.csv', root/'results.json'
    if any(path.is_symlink() for path in paths):
        raise ValueError('results.csv и results.json должны быть обычными файлами, а не ссылками')
    if not any(path.exists() for path in paths):
        return None, None
    if not all(path.is_file() for path in paths):
        raise ValueError('Датасет должен содержать оба файла: results.csv и results.json')
    return paths


@contextmanager
def dataset_lock(root):
    """Lock the directory itself, without leaving a lock file."""
    descriptor = os.open(root, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Этот датасет уже обновляется другим процессом') from None
        yield
    finally:
        os.close(descriptor)


def check_fresh(csv_path):
    """An entirely missing batch must not erase existing observations."""
    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or 'time_utc' not in reader.fieldnames:
            raise ValueError('Свежий CSV не содержит time_utc')
        for row in reader:
            if None in row or None in row.values():
                raise ValueError('Повреждённая строка свежего CSV')
            for key, value in row.items():
                if key == 'time_utc' or key.endswith('_count') or not value:
                    continue
                if math.isfinite(float(value)):
                    return
    raise ValueError('Свежая выгрузка не содержит числовых измерений; текущая база сохранена')


def publish(root, prepared):
    """Replace each file atomically; undo a failed update using temporary hard links.

    The caller holds dataset_lock. Readers should wait for successful completion:
    replacing two ordinary files cannot be a single atomic operation.
    """
    previous = current_files(root)
    names = ('results.csv', 'results.json')
    with tempfile.TemporaryDirectory(prefix='.previous-', dir=prepared) as temporary:
        backup = Path(temporary)
        for path in previous:
            if path is not None:
                os.link(path, backup/path.name)
        try:
            for name in names:
                os.replace(prepared/name, root/name)
        except BaseException:
            for name, old in zip(names, previous):
                if old is None:
                    (root/name).unlink(missing_ok=True)
                else:
                    os.replace(backup/name, root/name)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, description=__doc__)
    parser.add_argument('--out', type=Path, default=DATASETS)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('-h', '--help', action='store_true')
    args, download_args = parser.parse_known_args(argv)
    runner = TOOLS/'download_and_convert'/'run.sh'
    if args.help:
        print('Обновление датасета: выгрузка → CSV → append_data_tool + объединение JSON.\n'
              f'--out: каталог датасета (по умолчанию {DATASETS}).\n'
              'Остальные параметры совпадают с загрузчиком; --strict не публикует неполную выгрузку.\n', flush=True)
        return subprocess.run([str(runner), '--help']).returncode
    root = args.out.resolve()
    if args.plan_only:
        print(f'Целевой датасет: {root}\nПлан: download_and_convert → append_data_tool → results.csv и results.json', flush=True)
        return subprocess.run([str(runner), *download_args, '--plan-only', '--out', str(root)]).returncode
    root.mkdir(parents=True, exist_ok=True)
    with dataset_lock(root):
        old_csv, old_json = current_files(root)
        with tempfile.TemporaryDirectory(prefix='.staging-', dir=root) as temporary:
            stage = Path(temporary)
            fresh = stage/'fresh'
            result = subprocess.run([str(runner), *download_args, '--out', str(fresh)])
            if result.returncode:
                print('Выгрузка/конвертация завершилась с ненулевым кодом; текущий датасет не изменён.', file=sys.stderr)
                return result.returncode
            check_fresh(fresh/'results.csv')
            prepared = stage/'ready'
            prepared.mkdir()
            if old_csv is None:
                old_csv = stage/'empty.csv'
                old_csv.write_text('time_utc\n', encoding='utf-8')
            appended = subprocess.run([sys.executable, '-B', str(TOOLS/'append_data_tool'/'append_csv.py'),
                                       str(old_csv), str(fresh/'results.csv'), str(prepared/'results.csv')])
            if appended.returncode:
                return appended.returncode
            from json_dataset import merge_json
            print('Объединение JSON…', flush=True)
            merge_json(old_json, fresh/'results.json', prepared/'results.json')
            publish(root, prepared)
            print(f'Датасет обновлён: {root / "results.csv"}\nJSON: {root / "results.json"}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (OSError, ValueError, csv.Error) as error:
        print(f'Ошибка: {error}', file=sys.stderr)
        raise SystemExit(1)
