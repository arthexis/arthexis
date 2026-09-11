#!/usr/bin/env bash
set -Eeuo pipefail

redis_host="${REDIS_HOST:-127.0.0.1}"
redis_port="${REDIS_PORT:-6379}"
if [[ "${redis_host}" == "redis" || "${redis_host}" == "localhost" ]]; then
  redis_host="127.0.0.1"
fi

if command -v redis-cli >/dev/null 2>&1 \
  && redis-cli -h "${redis_host}" -p "${redis_port}" ping >/dev/null 2>&1; then
  echo "Redis already running on ${redis_host}:${redis_port}."
  exit 0
fi

sudo_cmd=()
if [[ "$(id -u)" != "0" ]]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo_cmd=(sudo -n)
  else
    echo "Passwordless sudo is required to install or start Redis for CI." >&2
    echo "Install/start redis-server manually, or run this helper as root." >&2
    exit 1
  fi
fi

run_with_timeout() {
  local seconds="$1"
  local description="$2"
  shift 2
  echo "${description} (timeout: ${seconds}s)..."
  timeout --foreground "${seconds}s" "$@"
}

apt_env=(env DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC)
apt_opts=(-o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)

if ! command -v redis-server >/dev/null 2>&1 || ! command -v redis-cli >/dev/null 2>&1; then
  run_with_timeout 600 "Updating apt metadata for Redis" \
    "${sudo_cmd[@]}" "${apt_env[@]}" apt-get "${apt_opts[@]}" update
  run_with_timeout 600 "Installing Redis packages" \
    "${sudo_cmd[@]}" "${apt_env[@]}" apt-get "${apt_opts[@]}" install -y --no-install-recommends redis-server
fi

if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  if ! run_with_timeout 30 "Starting Redis with systemd" \
    "${sudo_cmd[@]}" systemctl start redis-server; then
    echo "systemctl could not start Redis; falling back." >&2
  fi
fi
if ! redis-cli -h "${redis_host}" -p "${redis_port}" ping >/dev/null 2>&1 \
  && command -v service >/dev/null 2>&1; then
  if ! run_with_timeout 30 "Starting Redis with service" \
    "${sudo_cmd[@]}" service redis-server start; then
    echo "service could not start Redis; falling back to redis-server." >&2
  fi
fi
if ! redis-cli -h "${redis_host}" -p "${redis_port}" ping >/dev/null 2>&1; then
  echo "Starting Redis directly..."
  redis-server \
    --daemonize yes \
    --bind "${redis_host}" \
    --port "${redis_port}" \
    --save "" \
    --appendonly no
fi

echo "Waiting for Redis on ${redis_host}:${redis_port}..."
for _attempt in {1..15}; do
  if redis-cli -h "${redis_host}" -p "${redis_port}" ping >/dev/null 2>&1; then
    echo "Redis ready on ${redis_host}:${redis_port}."
    exit 0
  fi
  sleep 1
done

echo "Redis did not become ready on ${redis_host}:${redis_port}." >&2
exit 1
