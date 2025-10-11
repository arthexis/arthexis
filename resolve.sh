#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./resolve.sh [--file PATH | --text TEXT | TEXT...]

Resolve sigils in the provided text using the project's resolver. When no
arguments are supplied the script reads from standard input.

Options:
  -f, --file PATH   Resolve sigils in the contents of PATH.
      --text TEXT   Resolve sigils in the provided TEXT.
  -h, --help        Show this help message and exit.

Examples:
  ./resolve.sh --text "Hello [SYS.version]"
  ./resolve.sh --file template.txt
  cat template.txt | ./resolve.sh
USAGE
}

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_CMD="${PYTHON:-python}"

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "Python interpreter '$PYTHON_CMD' not found." >&2
  exit 1
fi

file=""
explicit_text=""
declare -a positional_text=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--file)
      if [[ -n "$explicit_text" || ${#positional_text[@]} -gt 0 ]]; then
        echo "--file cannot be combined with text arguments." >&2
        exit 1
      fi
      if [[ $# -lt 2 ]]; then
        echo "Missing argument for --file." >&2
        exit 1
      fi
      file="$2"
      shift 2
      ;;
    --text)
      if [[ -n "$file" ]]; then
        echo "--text cannot be combined with --file." >&2
        exit 1
      fi
      if [[ -n "$explicit_text" ]]; then
        echo "Multiple --text values provided." >&2
        exit 1
      fi
      if [[ $# -lt 2 ]]; then
        echo "Missing argument for --text." >&2
        exit 1
      fi
      explicit_text="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      positional_text+=("$@")
      break
      ;;
    *)
      if [[ -n "$file" ]]; then
        echo "Positional text cannot be combined with --file." >&2
        exit 1
      fi
      positional_text+=("$1")
      shift
      ;;
  esac
 done

if [[ -n "$file" ]]; then
  "$PYTHON_CMD" "$BASE_DIR/scripts/resolve_sigils.py" --file "$file"
elif [[ -n "$explicit_text" ]]; then
  "$PYTHON_CMD" "$BASE_DIR/scripts/resolve_sigils.py" --text "$explicit_text"
elif [[ ${#positional_text[@]} -gt 0 ]]; then
  "$PYTHON_CMD" "$BASE_DIR/scripts/resolve_sigils.py" --text "${positional_text[*]}"
else
  "$PYTHON_CMD" "$BASE_DIR/scripts/resolve_sigils.py"
fi
