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

if ! command -v redis-server >/dev/null 2>&1 || ! command -v redis-cli >/dev/null 2>&1; then
  "${sudo_cmd[@]}" apt-get update
  "${sudo_cmd[@]}" apt-get install -y --no-install-recommends redis-server
fi

if command -v systemctl >/dev/null 2>&1; then
  "${sudo_cmd[@]}" systemctl start redis-server >/dev/null 2>&1 || true
fi
if ! redis-cli -h "${redis_host}" -p "${redis_port}" ping >/dev/null 2>&1 \
  && command -v service >/dev/null 2>&1; then
  "${sudo_cmd[@]}" service redis-server start >/dev/null 2>&1 || true
fi
if ! redis-cli -h "${redis_host}" -p "${redis_port}" ping >/dev/null 2>&1; then
  redis-server \
    --daemonize yes \
    --bind "${redis_host}" \
    --port "${redis_port}" \
    --save "" \
    --appendonly no
fi

for _attempt in {1..15}; do
  if redis-cli -h "${redis_host}" -p "${redis_port}" ping >/dev/null 2>&1; then
    echo "Redis ready on ${redis_host}:${redis_port}."
    exit 0
  fi
  sleep 1
done

echo "Redis did not become ready on ${redis_host}:${redis_port}." >&2
exit 1
