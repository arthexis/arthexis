#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${ARTHEXIS_DB_BACKEND:-sqlite}" != "postgres" ]]; then
  echo "PostgreSQL not required for ARTHEXIS_DB_BACKEND=${ARTHEXIS_DB_BACKEND:-sqlite}."
  exit 0
fi

postgres_db="${POSTGRES_DB:-arthexis}"
postgres_user="${POSTGRES_USER:-postgres}"
postgres_password="${POSTGRES_PASSWORD:-postgres}"
postgres_host="${POSTGRES_HOST:-127.0.0.1}"
postgres_port="${POSTGRES_PORT:-5432}"

sudo_cmd=()
if command -v sudo >/dev/null 2>&1; then
  sudo_cmd=(sudo)
fi

run_with_timeout() {
  local seconds="$1"
  local description="$2"
  shift 2
  echo "${description} (timeout: ${seconds}s)..."
  timeout --foreground "${seconds}s" "$@"
}

if ! command -v psql >/dev/null 2>&1 || ! command -v pg_isready >/dev/null 2>&1; then
  run_with_timeout 180 "Updating apt metadata for PostgreSQL" "${sudo_cmd[@]}" apt-get update
  run_with_timeout 180 "Installing PostgreSQL packages" \
    "${sudo_cmd[@]}" apt-get install -y --no-install-recommends postgresql postgresql-client
fi

if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  if ! run_with_timeout 30 "Starting PostgreSQL with systemd" \
    "${sudo_cmd[@]}" systemctl start postgresql; then
    echo "systemctl could not start PostgreSQL; falling back to service." >&2
  fi
fi
if ! pg_isready -h "${postgres_host}" -p "${postgres_port}" >/dev/null 2>&1 \
  && command -v service >/dev/null 2>&1; then
  if ! run_with_timeout 30 "Starting PostgreSQL with service" \
    "${sudo_cmd[@]}" service postgresql start; then
    echo "service could not start PostgreSQL; checking readiness before failing." >&2
  fi
fi

echo "Waiting for PostgreSQL on ${postgres_host}:${postgres_port}..."
for _attempt in {1..15}; do
  if pg_isready -h "${postgres_host}" -p "${postgres_port}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! pg_isready -h "${postgres_host}" -p "${postgres_port}" >/dev/null 2>&1; then
  echo "PostgreSQL did not become ready on ${postgres_host}:${postgres_port}." >&2
  exit 1
fi

psql_as_postgres=()
if command -v sudo >/dev/null 2>&1; then
  psql_as_postgres=(sudo -u postgres psql)
elif command -v runuser >/dev/null 2>&1; then
  psql_as_postgres=(runuser -u postgres -- psql)
else
  psql_as_postgres=(psql -U postgres)
fi

echo "Configuring PostgreSQL role and database (timeout: 30s)..."
timeout --foreground 30s "${psql_as_postgres[@]}" \
  -v ON_ERROR_STOP=1 \
  -v postgres_user="${postgres_user}" \
  -v postgres_password="${postgres_password}" \
  -v postgres_db="${postgres_db}" <<'SQL'
SELECT format(
  'CREATE ROLE %I WITH LOGIN PASSWORD %L',
  :'postgres_user',
  :'postgres_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'postgres_user')\gexec
ALTER ROLE :"postgres_user" WITH PASSWORD :'postgres_password';
SELECT format('CREATE DATABASE %I', :'postgres_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'postgres_db')\gexec
SQL

echo "Verifying PostgreSQL connection (timeout: 30s)..."
timeout --foreground 30s env PGPASSWORD="${postgres_password}" psql \
  -h "${postgres_host}" \
  -p "${postgres_port}" \
  -U "${postgres_user}" \
  -d "${postgres_db}" \
  -c "SELECT 1" >/dev/null

echo "PostgreSQL ready on ${postgres_host}:${postgres_port}/${postgres_db}."
