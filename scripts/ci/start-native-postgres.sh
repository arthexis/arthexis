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

if ! command -v psql >/dev/null 2>&1 || ! command -v pg_isready >/dev/null 2>&1; then
  "${sudo_cmd[@]}" apt-get update
  "${sudo_cmd[@]}" apt-get install -y --no-install-recommends postgresql postgresql-client
fi

if command -v systemctl >/dev/null 2>&1; then
  "${sudo_cmd[@]}" systemctl start postgresql >/dev/null 2>&1 || true
fi
if ! pg_isready -h "${postgres_host}" -p "${postgres_port}" >/dev/null 2>&1 \
  && command -v service >/dev/null 2>&1; then
  "${sudo_cmd[@]}" service postgresql start >/dev/null 2>&1 || true
fi

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

"${psql_as_postgres[@]}" \
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

PGPASSWORD="${postgres_password}" psql \
  -h "${postgres_host}" \
  -p "${postgres_port}" \
  -U "${postgres_user}" \
  -d "${postgres_db}" \
  -c "SELECT 1" >/dev/null

echo "PostgreSQL ready on ${postgres_host}:${postgres_port}/${postgres_db}."
