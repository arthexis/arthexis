#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/locks"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/$(basename "$0" .sh).lock"
exec 200>"$LOCK_FILE"
flock -n 200 || { echo "Another instance of $(basename "$0") is running." >&2; exit 1; }

usage() {
    cat <<USAGE
Usage: $0 [--clean]

Configure or remove PostgreSQL database settings for this project.
Without arguments the script will configure the database.
  --clean, --remove  Remove the database configuration and drop the database/user.
  -h, --help         Show this help message and exit.
USAGE
}

REMOVE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean|--remove)
            REMOVE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

ensure_psql() {
    if ! command -v psql >/dev/null 2>&1; then
        echo "psql (PostgreSQL client) is required." >&2
        exit 1
    fi
}

CONFIG_FILE="$BASE_DIR/postgres.env"

if [[ $REMOVE -eq 1 ]]; then
    ensure_psql
    if [[ -f "$CONFIG_FILE" ]]; then
        source "$CONFIG_FILE"
    else
        echo "Configuration file not found: $CONFIG_FILE" >&2
        exit 1
    fi

    # Require root only when managing a local server via the postgres OS user
    if id -u postgres >/dev/null 2>&1 && [[ "$POSTGRES_HOST" == "localhost" || "$POSTGRES_HOST" == "127.0.0.1" ]]; then
        if [[ $EUID -ne 0 ]]; then
            echo "This script must be run as root to manage the local PostgreSQL server" >&2
            exit 1
        fi
    fi

    read -rp "Drop database '$POSTGRES_DB'? [y/N]: " CONFIRM
    if [[ "${CONFIRM,,}" == "y" ]]; then
        if id -u postgres >/dev/null 2>&1 && [[ "$POSTGRES_HOST" == "localhost" || "$POSTGRES_HOST" == "127.0.0.1" ]]; then
            sudo -u postgres psql -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"
            sudo -u postgres psql -c "DROP USER IF EXISTS \"$POSTGRES_USER\";"
        else
            read -rp "Database admin user [postgres]: " DB_ADMIN
            DB_ADMIN=${DB_ADMIN:-postgres}
            read -rsp "Database admin password: " DB_ADMIN_PASS
            echo
            PGPASSWORD="$DB_ADMIN_PASS" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$DB_ADMIN" -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"
            PGPASSWORD="$DB_ADMIN_PASS" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$DB_ADMIN" -c "DROP USER IF EXISTS \"$POSTGRES_USER\";"
        fi
        rm -f "$CONFIG_FILE"
        echo "Configuration removed."
    else
        echo "Aborted."
    fi
    exit 0
fi

ensure_psql

read -rp "Database name [arthexis]: " DB_NAME
DB_NAME=${DB_NAME:-arthexis}
read -rp "Database user [arthexis]: " DB_USER
DB_USER=${DB_USER:-arthexis}
read -rsp "Database password: " DB_PASS
echo
read -rp "Database host [localhost]: " DB_HOST
DB_HOST=${DB_HOST:-localhost}
read -rp "Database port [5432]: " DB_PORT
DB_PORT=${DB_PORT:-5432}

# Require root only when using local postgres user
if id -u postgres >/dev/null 2>&1 && [[ "$DB_HOST" == "localhost" || "$DB_HOST" == "127.0.0.1" ]] && [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root to manage the local PostgreSQL server" >&2
    exit 1
fi

if id -u postgres >/dev/null 2>&1 && [[ "$DB_HOST" == "localhost" || "$DB_HOST" == "127.0.0.1" ]]; then
    sudo -u postgres psql <<SQL
DO
\$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_user WHERE usename = '$DB_USER') THEN
       CREATE USER "$DB_USER" WITH PASSWORD '$DB_PASS';
   END IF;
END
\$\$;
DO
\$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname = '$DB_NAME') THEN
       CREATE DATABASE "$DB_NAME" OWNER "$DB_USER";
   END IF;
END
\$\$;
SQL
else
    read -rp "Database admin user [postgres]: " DB_ADMIN
    DB_ADMIN=${DB_ADMIN:-postgres}
    read -rsp "Database admin password: " DB_ADMIN_PASS
    echo
    PGPASSWORD="$DB_ADMIN_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_ADMIN" <<SQL
DO
\$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_user WHERE usename = '$DB_USER') THEN
       CREATE USER "$DB_USER" WITH PASSWORD '$DB_PASS';
   END IF;
END
\$\$;
DO
\$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname = '$DB_NAME') THEN
       CREATE DATABASE "$DB_NAME" OWNER "$DB_USER";
   END IF;
END
\$\$;
SQL
fi

cat > "$CONFIG_FILE" <<ENV
POSTGRES_DB=$DB_NAME
POSTGRES_USER=$DB_USER
POSTGRES_PASSWORD=$DB_PASS
POSTGRES_HOST=$DB_HOST
POSTGRES_PORT=$DB_PORT
ENV

chmod 600 "$CONFIG_FILE"

echo "PostgreSQL configuration saved to $CONFIG_FILE"

