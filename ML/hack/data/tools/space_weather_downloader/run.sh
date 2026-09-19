#!/bin/sh
# Works from any current directory; installs project dependencies on first run.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_bin="$project_dir/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
    runtime=""
    for candidate in python3.11 python3.12 python3.10 python3.9 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import ssl,sys; assert (3,9) <= sys.version_info[:2] <= (3,12); assert ssl.OPENSSL_VERSION.startswith("OpenSSL ") and ssl.OPENSSL_VERSION_INFO >= (1,1,1)' >/dev/null 2>&1; then
            runtime="$candidate"
            break
        fi
    done
    if [ -z "$runtime" ]; then
        echo 'Для зафиксированных пакетов нужен Python 3.9–3.12 с OpenSSL 1.1.1+ (рекомендуется 3.11/3.12).' >&2
        exit 1
    fi
    "$runtime" -m venv "$project_dir/.venv"
fi
if ! "$python_bin" -c 'import ssl; assert ssl.OPENSSL_VERSION.startswith("OpenSSL ") and ssl.OPENSSL_VERSION_INFO >= (1,1,1)' >/dev/null 2>&1; then
    echo 'Существующее .venv использует неподдерживаемый TLS. Сохраните его и пересоздайте через Python с OpenSSL.' >&2
    exit 1
fi
if ! "$python_bin" -c 'import requests, numpy, netCDF4' >/dev/null 2>&1; then
    "$python_bin" -m pip install -r "$project_dir/requirements.txt"
fi
exec "$python_bin" -B "$project_dir/download_space_weather.py" "$@"
