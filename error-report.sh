#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

# Lifecycle CLI contract: keep help/options aligned with docs/development/install-lifecycle-scripts-manual.md.
usage() {
  cat <<'EOF'
Usage: ./error-report.sh [options]

Options:
  --output-dir DIR          Directory for generated zip files. Defaults to work/error-reports.
  --since DURATION          Only include non-critical logs modified within DURATION, such as 12h or 7d.
  --max-log-files COUNT     Maximum number of log files to include. Defaults to 30.
  --max-file-bytes BYTES    Maximum bytes copied from each text file. Defaults to 262144.
  --upload-url URL          Upload the generated zip to an explicit signed URL.
  --upload-method METHOD    Upload HTTP method, PUT or POST. Defaults to PUT.
  --upload-timeout SECONDS  Upload timeout in seconds. Defaults to 60.
  --allow-insecure-upload   Permit http:// upload URLs. HTTPS is required by default.
  --dry-run                 Print the collection plan without writing a zip or uploading.
  -h, --help                Show this help message and exit.
EOF
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "Python 3 is required to build an Arthexis error report." >&2
  exit 1
fi

exec "$PYTHON_BIN" "$BASE_DIR/scripts/error_report.py" --base-dir "$BASE_DIR" "$@"
