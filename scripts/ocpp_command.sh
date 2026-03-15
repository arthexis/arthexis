#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ocpp_command.sh <product> <command> [-- command args]

Run command-style products from a single CLI entrypoint.

Examples:
  ./scripts/ocpp_command.sh ocpp_cli_simulator simulate -- --slot 1 --cp-path CP2
  PYTHON=./.venv/bin/python ./scripts/ocpp_command.sh ocpp_charger_web preview
USAGE
}

if [[ $# -lt 2 ]]; then
  usage
  exit 1
fi

product="$1"
command_name="$2"
shift 2

if [[ "${1:-}" == "--" ]]; then
  shift
fi

python_cmd="${PYTHON:-python}"

case "$product:$command_name" in
  ocpp_cli_simulator:simulate)
    exec "$python_cmd" manage.py simulator start "$@"
    ;;
  ocpp_charger_web:preview)
    preview_host="127.0.0.1"
    preview_port="8899"
    preview_base_url="http://${preview_host}:${preview_port}"
    preview_path="/ocpp/cpms/dashboard/"
    preview_output_dir="preview_output"

    tmp_html="$(mktemp)"
    server_log="$(mktemp)"
    server_pid=""

    cleanup() {
      if [[ -n "$server_pid" ]] && kill -0 "$server_pid" >/dev/null 2>&1; then
        kill "$server_pid" >/dev/null 2>&1 || true
      fi
      rm -f "$tmp_html" "$server_log"
    }
    trap cleanup EXIT

    "$python_cmd" manage.py runserver "${preview_host}:${preview_port}" --noreload --no-celery >"$server_log" 2>&1 &
    server_pid="$!"

    ready=0
    for _ in $(seq 1 240); do
      if ! kill -0 "$server_pid" >/dev/null 2>&1; then
        break
      fi
      http_code="$(curl -sS -L -o "$tmp_html" -w "%{http_code}" "${preview_base_url}${preview_path}" || true)"
      if [[ "$http_code" == "200" || "$http_code" == "302" ]]; then
        ready=1
        break
      fi
      if [[ "$http_code" == "404" ]]; then
        echo "Preview precheck failed: ${preview_path} returned 404." >&2
        cat "$server_log" >&2
        exit 1
      fi
      sleep 1
    done

    if [[ "$ready" -ne 1 ]]; then
      echo "Preview server failed to become ready for ${preview_path}" >&2
      cat "$server_log" >&2
      exit 1
    fi

    if grep -Eiq "not found|404|page not found" "$tmp_html"; then
      echo "Preview precheck failed: route resolved to a not-found page." >&2
      exit 1
    fi

    "$python_cmd" manage.py preview --base-url "$preview_base_url" --path "$preview_path" --output-dir "$preview_output_dir" "$@"

    "$python_cmd" scripts/preview_guard.py --output-dir "$preview_output_dir" --html-file "$tmp_html"
    ;;
  *)
    echo "Unknown product/command combination: $product:$command_name" >&2
    echo "Known combinations:" >&2
    echo "  ocpp_cli_simulator:simulate" >&2
    echo "  ocpp_charger_web:preview" >&2
    exit 2
    ;;
esac
