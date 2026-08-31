#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CHARGER_8900_ENV_FILE:-$BASE_DIR/.private/charger-8900.env}"
TEMPLATE_FILE="${CHARGER_8900_TEMPLATE_FILE:-$BASE_DIR/config/templates/charger-8900.env.template}"

CLEANUP_FILES=()
TCPDUMP_PID=""

cleanup() {
  if [[ -n "${TCPDUMP_PID:-}" ]]; then
    sudo kill -INT "$TCPDUMP_PID" 2>/dev/null || true
    wait "$TCPDUMP_PID" 2>/dev/null || true
    TCPDUMP_PID=""
  fi
  if [[ ${#CLEANUP_FILES[@]} -gt 0 ]]; then
    rm -f -- "${CLEANUP_FILES[@]}"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: scripts/charger_8900_http.sh <command> [path]

Commands:
  env-path       Print the env file path this tool will load.
  init-env       Create or repair the env file from the tracked template.
  check-env      Show non-secret charger/env status.
  probe          Run the cautious single-port SYN probe with tcpdump capture.
  headers [/]    One unauthenticated HTTP/1.0 GET; print headers only.
  auth-headers [/]  One authenticated HTTP/1.0 GET; print headers only.
  get [/]        Authenticated HTTP/1.0 GET; print response body.

Env file:
  Defaults to ./.private/charger-8900.env.
  Override with CHARGER_8900_ENV_FILE=/path/to/file.
  Missing env files are recreated from ./config/templates/charger-8900.env.template.

Required connection values:
  CHARGER_8900_HOST=192.168.129.191
  CHARGER_8900_PORT=8900

Credentials, only required for auth-headers/get:
  CHARGER_8900_USER=
  CHARGER_8900_PASSWORD=

Optional:
  CHARGER_8900_INTERFACE=eth0
  CHARGER_8900_URL=http://192.168.129.191:8900
  CHARGER_8900_CONNECT_TIMEOUT=1
  CHARGER_8900_MAX_TIME=2
  CHARGER_8900_LOG_DIR=logs/charger_8900_manual
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

env_assignment_key() {
  local line key
  line="$(trim "$1")"
  [[ -z "$line" || "${line:0:1}" == "#" ]] && return 1
  [[ "$line" == export\ * ]] && line="${line#export }"
  [[ "$line" == *=* ]] || return 1
  key="$(trim "${line%%=*}")"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 1
  printf '%s' "$key"
}

env_has_key() {
  local wanted="$1"
  local line key
  [[ -f "$ENV_FILE" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    key="$(env_assignment_key "$line" || true)"
    [[ "$key" == "$wanted" ]] && return 0
  done < "$ENV_FILE"
  return 1
}

ensure_env_file() {
  [[ ! -L "$ENV_FILE" ]] || die "refusing to use symlinked env file: $ENV_FILE"

  if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$TEMPLATE_FILE" ]] || die "template env file not found: $TEMPLATE_FILE"
    mkdir -p "$(dirname "$ENV_FILE")"
    umask 077
    cp "$TEMPLATE_FILE" "$ENV_FILE"
    chmod 600 "$ENV_FILE" || die "failed to enforce 0600 on env file: $ENV_FILE"
    printf 'Created %s from %s\n' "$ENV_FILE" "$TEMPLATE_FILE" >&2
  else
    chmod 600 "$ENV_FILE" || die "failed to enforce 0600 on env file: $ENV_FILE"
  fi

  append_missing_template_keys
}

append_missing_template_keys() {
  [[ -f "$TEMPLATE_FILE" ]] || return 0

  local line key wrote_header=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    key="$(env_assignment_key "$line" || true)"
    [[ -n "$key" ]] || continue
    env_has_key "$key" && continue

    if [[ "$wrote_header" -eq 0 ]]; then
      {
        printf '\n'
        printf '# Added from %s on %s.\n' "$(basename "$TEMPLATE_FILE")" "$(date +%Y-%m-%d)"
      } >> "$ENV_FILE"
      wrote_header=1
    fi
    printf '%s\n' "$line" >> "$ENV_FILE"
  done < "$TEMPLATE_FILE"
}

load_env_file() {
  [[ -f "$ENV_FILE" ]] || return 0

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="$(trim "$line")"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    [[ "$line" == export\ * ]] && line="${line#export }"
    [[ "$line" == *=* ]] || continue

    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ "$key" == CHARGER_8900_* ]] || continue

    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi

    export "$key=$value"
  done < "$ENV_FILE"
}

require_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] || die "CHARGER_8900_PORT must be numeric"
  (( ${#port} <= 5 )) || die "CHARGER_8900_PORT must be 1..65535"

  local port_number=$((10#$port))
  (( port_number >= 1 && port_number <= 65535 )) || die "CHARGER_8900_PORT must be 1..65535"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed"
}

require_value() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "$name is required"
}

base_url() {
  if [[ -n "${CHARGER_8900_URL:-}" ]]; then
    printf '%s' "${CHARGER_8900_URL%/}"
    return
  fi

  require_value CHARGER_8900_HOST
  local port="${CHARGER_8900_PORT:-8900}"
  require_port "$port"
  printf 'http://%s:%s' "$CHARGER_8900_HOST" "$port"
}

request_url() {
  local path="${1:-/}"
  [[ "$path" == /* ]] || path="/$path"
  local url
  url="$(base_url)"
  printf '%s%s' "$url" "$path"
}

curl_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/}"
  printf '"%s"' "$value"
}

auth_config_file() {
  require_value CHARGER_8900_USER
  require_value CHARGER_8900_PASSWORD

  local result_var="$1"
  local generated_auth_file
  generated_auth_file="$(mktemp "${TMPDIR:-/tmp}/charger-8900-curl.XXXXXX")"
  CLEANUP_FILES+=("$generated_auth_file")
  chmod 600 "$generated_auth_file"
  {
    printf 'user = '
    curl_quote "${CHARGER_8900_USER}:${CHARGER_8900_PASSWORD}"
    printf '\n'
  } > "$generated_auth_file"
  printf -v "$result_var" '%s' "$generated_auth_file"
}

curl_headers() {
  local path="${1:-/}"
  local use_auth="${2:-0}"
  local auth_file=""
  local url

  url="$(request_url "$path")"

  if [[ "$use_auth" == "1" ]]; then
    auth_config_file auth_file
  fi

  local args=(
    --http1.0
    --no-keepalive
    --connect-timeout "${CHARGER_8900_CONNECT_TIMEOUT:-1}"
    --max-time "${CHARGER_8900_MAX_TIME:-2}"
    -sS
    -D -
    -o /dev/null
  )
  [[ -z "$auth_file" ]] || args+=(--config "$auth_file")

  curl "${args[@]}" "$url"
}

curl_get() {
  local path="${1:-/}"
  local auth_file
  local url

  url="$(request_url "$path")"
  auth_config_file auth_file

  curl \
    --http1.0 \
    --no-keepalive \
    --connect-timeout "${CHARGER_8900_CONNECT_TIMEOUT:-1}" \
    --max-time "${CHARGER_8900_MAX_TIME:-2}" \
    -sS \
    --config "$auth_file" \
    "$url"
}

safe_probe() {
  require_command nmap
  require_command tcpdump
  require_command sudo
  require_value CHARGER_8900_HOST
  local port="${CHARGER_8900_PORT:-8900}"
  require_port "$port"
  sudo -v || die "sudo authentication failed"

  local iface="${CHARGER_8900_INTERFACE:-eth0}"
  local log_dir="${CHARGER_8900_LOG_DIR:-logs/charger_8900_manual}"
  [[ "$log_dir" == /* ]] || log_dir="$BASE_DIR/$log_dir"
  mkdir -p "$log_dir"

  local stamp pcap nmap_out tcpdump_log
  stamp="$(date +%Y%m%dT%H%M%S%z)"
  pcap="$log_dir/${stamp}-syn-8900.pcap"
  nmap_out="$log_dir/${stamp}-syn-8900.nmap.txt"
  tcpdump_log="$log_dir/${stamp}-syn-8900.tcpdump.log"

  sudo timeout 120 tcpdump -i "$iface" -nn -s0 \
    -w "$pcap" \
    "host $CHARGER_8900_HOST and (arp or tcp port ${port})" \
    > "$tcpdump_log" 2>&1 &
  TCPDUMP_PID=$!
  sleep 1

  sudo nmap -Pn -n -sS --reason --max-retries 0 --host-timeout 5s \
    -p "${port}" "$CHARGER_8900_HOST" | tee "$nmap_out"

  sleep 1
  sudo kill -INT "$TCPDUMP_PID" 2>/dev/null || true
  wait "$TCPDUMP_PID" 2>/dev/null || true
  TCPDUMP_PID=""
  sudo chown "$(id -u):$(id -g)" "$pcap" 2>/dev/null || true

  printf '\nSaved probe artifacts:\n'
  printf '  %s\n' "$nmap_out" "$pcap" "$tcpdump_log"
}

check_env() {
  local url

  printf 'Env file: %s\n' "$ENV_FILE"
  printf 'Template file: %s\n' "$TEMPLATE_FILE"
  if [[ -f "$ENV_FILE" ]]; then
    printf 'Env file present: yes\n'
  else
    printf 'Env file present: no\n'
  fi
  url="$(base_url)"
  printf 'URL: %s\n' "$url"
  printf 'Interface: %s\n' "${CHARGER_8900_INTERFACE:-eth0}"
  if [[ -n "${CHARGER_8900_USER:-}" ]]; then
    printf 'Username set: yes\n'
  else
    printf 'Username set: no\n'
  fi
  if [[ -n "${CHARGER_8900_PASSWORD:-}" ]]; then
    printf 'Password set: yes\n'
  else
    printf 'Password set: no\n'
  fi
}

main() {
  local command="${1:-help}"
  shift || true

  case "$command" in
    env-path)
      printf '%s\n' "$ENV_FILE"
      ;;
    init-env)
      ensure_env_file
      load_env_file
      check_env
      ;;
    check-env)
      ensure_env_file
      load_env_file
      check_env
      ;;
    probe)
      ensure_env_file
      load_env_file
      safe_probe
      ;;
    headers)
      ensure_env_file
      load_env_file
      curl_headers "${1:-/}" 0
      ;;
    auth-headers)
      ensure_env_file
      load_env_file
      curl_headers "${1:-/}" 1
      ;;
    get)
      ensure_env_file
      load_env_file
      curl_get "${1:-/}"
      ;;
    help|--help|-h)
      usage
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
