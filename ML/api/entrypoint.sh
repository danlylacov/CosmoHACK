#!/bin/sh
set -eu
dest="${SEPNET_DATA:-/app/data/datasets}"
src="/opt/bundled-datasets"

mkdir -p "$dest"
if [ ! -s "$dest/results.csv" ] && [ -s "$src/results.csv" ]; then
    echo "Seeding /app/data/datasets from the image bundle"
    cp -a "$src/." "$dest/"
fi

if [ -f "$dest/results.csv" ] && head -n 1 "$dest/results.csv" | grep -q 'git-lfs'; then
    echo "results.csv is a Git LFS pointer. Run 'git lfs pull' before docker build." >&2
fi

exec "$@"
