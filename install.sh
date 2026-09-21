#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
import_source=""

if [[ "$#" -gt 0 ]]; then
    if [[ "$#" -ne 2 || "$1" != "--import" ]]; then
        echo "Usage: $0 [--import /path/to/legacy.sqlite3]" >&2
        exit 64
    fi
    import_source="$2"
fi
data_dir="${ARTHEXIS_DATA_DIR:-$root_dir/var}"
database_path="${ARTHEXIS_DATABASE_PATH:-$data_dir/db.sqlite3}"
venv_dir="$root_dir/.venv"

mkdir -p "$data_dir"
if [[ ! -x "$venv_dir/bin/python" ]]; then
    python3 -m venv "$venv_dir"
fi

"$venv_dir/bin/python" -m pip install --requirement "$root_dir/requirements.txt"
"$venv_dir/bin/python" -c "from pathlib import Path; from arthexis.reconciliation.source import classify_database; import sys; status = classify_database(Path(sys.argv[1])); print(f'Database classification: {status}'); raise SystemExit(0 if status in {'fresh', 'v2'} else 2)" "$database_path"
ARTHEXIS_DATA_DIR="$data_dir" ARTHEXIS_DATABASE_PATH="$database_path" \
    "$venv_dir/bin/python" "$root_dir/manage.py" migrate --noinput
ARTHEXIS_DATA_DIR="$data_dir" ARTHEXIS_DATABASE_PATH="$database_path" \
    "$venv_dir/bin/python" "$root_dir/manage.py" seed

if [[ -n "$import_source" ]]; then
    ARTHEXIS_DATA_DIR="$data_dir" ARTHEXIS_DATABASE_PATH="$database_path" \
        "$venv_dir/bin/python" "$root_dir/scripts/reconcile.py" import \
        --database "$import_source"
fi
