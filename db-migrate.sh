#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/helpers/logging.sh
. "$BASE_DIR/scripts/helpers/logging.sh"
arthexis_resolve_log_dir "$BASE_DIR" LOG_DIR || exit 1
LOG_FILE="$LOG_DIR/$(basename "$0" .sh).log"
exec > >(tee "$LOG_FILE") 2>&1
cd "$BASE_DIR"

LOCK_DIR="$BASE_DIR/.locks"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/$(basename "$0" .sh).lock"
exec 200>"$LOCK_FILE"
flock -n 200 || { echo "Another instance of $(basename "$0") is running." >&2; exit 1; }

usage() {
    cat <<USAGE
Usage: $0 --from <sqlite|postgres> --to <sqlite|postgres> [options]

Options:
  --sqlite-path PATH       Path to the SQLite database (default: $BASE_DIR/db.sqlite3).
  --postgres-env FILE      Load PostgreSQL credentials from FILE (default: $BASE_DIR/postgres.env if present).
  --dump-file FILE         Write the intermediate JSON dump to FILE instead of a temporary file.
  --keep-dump              Keep the generated dump file after completion.
  --force                  Replace the destination database if it already contains data.
  -h, --help               Show this help message and exit.
USAGE
}

SOURCE=""
DEST=""
SQLITE_PATH="$BASE_DIR/db.sqlite3"
POSTGRES_ENV_FILE=""
USER_DUMP_FILE=""
KEEP_DUMP=0
FORCE=0
CURRENT_DB_CONTEXT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)
            [[ $# -lt 2 ]] && { echo "Missing value for --from" >&2; usage >&2; exit 1; }
            SOURCE="$2"
            shift 2
            ;;
        --to)
            [[ $# -lt 2 ]] && { echo "Missing value for --to" >&2; usage >&2; exit 1; }
            DEST="$2"
            shift 2
            ;;
        --sqlite-path)
            [[ $# -lt 2 ]] && { echo "Missing value for --sqlite-path" >&2; usage >&2; exit 1; }
            SQLITE_PATH="$2"
            shift 2
            ;;
        --postgres-env)
            [[ $# -lt 2 ]] && { echo "Missing value for --postgres-env" >&2; usage >&2; exit 1; }
            POSTGRES_ENV_FILE="$2"
            shift 2
            ;;
        --dump-file)
            [[ $# -lt 2 ]] && { echo "Missing value for --dump-file" >&2; usage >&2; exit 1; }
            USER_DUMP_FILE="$2"
            KEEP_DUMP=1
            shift 2
            ;;
        --keep-dump)
            KEEP_DUMP=1
            shift
            ;;
        --force)
            FORCE=1
            shift
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
done

shopt -s nocasematch
case "$SOURCE" in
    sqlite|postgres)
        SOURCE=${SOURCE,,}
        ;;
    "")
        echo "--from option is required" >&2
        usage >&2
        exit 1
        ;;
    *)
        echo "Unsupported source backend: $SOURCE" >&2
        usage >&2
        exit 1
        ;;
 esac

case "$DEST" in
    sqlite|postgres)
        DEST=${DEST,,}
        ;;
    "")
        echo "--to option is required" >&2
        usage >&2
        exit 1
        ;;
    *)
        echo "Unsupported destination backend: $DEST" >&2
        usage >&2
        exit 1
        ;;
 esac
shopt -u nocasematch

if [[ "$SOURCE" == "$DEST" ]]; then
    echo "Source and destination backends must differ" >&2
    exit 1
fi

if [[ -z "$POSTGRES_ENV_FILE" && -f "$BASE_DIR/postgres.env" ]]; then
    POSTGRES_ENV_FILE="$BASE_DIR/postgres.env"
fi

info() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

error() {
    printf '[%s] ERROR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
}

load_postgres_env() {
    local context="$1"
    if [[ -n "$POSTGRES_ENV_FILE" ]]; then
        if [[ ! -f "$POSTGRES_ENV_FILE" ]]; then
            error "PostgreSQL environment file not found: $POSTGRES_ENV_FILE"
            exit 1
        fi
        set -a
        # shellcheck disable=SC1090
        source "$POSTGRES_ENV_FILE"
        set +a
    fi

    local missing=()
    local var
    for var in POSTGRES_DB POSTGRES_USER POSTGRES_HOST POSTGRES_PORT; do
        if [[ -z "${!var:-}" ]]; then
            missing+=("$var")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing PostgreSQL environment variables for $context database: ${missing[*]}"
        exit 1
    fi

    if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
        export PGPASSWORD="$POSTGRES_PASSWORD"
    else
        unset PGPASSWORD 2>/dev/null || true
    fi
}

clear_postgres_env() {
    unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT PGPASSWORD
}

run_manage() {
    local backend="$1"
    shift
    local context="${CURRENT_DB_CONTEXT:-$backend}"
    (
        set -euo pipefail
        export DJANGO_SETTINGS_MODULE=${DJANGO_SETTINGS_MODULE:-config.settings}
        clear_postgres_env
        if [[ "$backend" == "postgres" ]]; then
            load_postgres_env "$context"
            unset ARTHEXIS_SQLITE_PATH
        else
            export ARTHEXIS_SQLITE_PATH="$SQLITE_PATH"
        fi
        export ARTHEXIS_FORCE_DB_BACKEND="$backend"
        ./manage.py "$@"
    )
}

ensure_dump_path() {
    local path="$1"
    local dir
    dir="$(dirname "$path")"
    mkdir -p "$dir"
    : > "$path"
}

if [[ "$SOURCE" == "sqlite" ]]; then
    if [[ ! -f "$SQLITE_PATH" ]]; then
        error "SQLite database not found: $SQLITE_PATH"
        exit 1
    fi
elif [[ "$SOURCE" == "postgres" ]]; then
    load_postgres_env "source"
fi

if [[ "$DEST" == "sqlite" ]]; then
    if [[ -f "$SQLITE_PATH" ]]; then
        if [[ $FORCE -eq 1 ]]; then
            info "Removing existing SQLite database at $SQLITE_PATH"
            rm -f "$SQLITE_PATH"
        else
            error "SQLite database $SQLITE_PATH already exists. Use --force to overwrite it."
            exit 1
        fi
    fi
elif [[ "$DEST" == "postgres" ]]; then
    load_postgres_env "destination"
fi

DUMP_PATH=""
CLEANUP_DUMP=0
if [[ -n "$USER_DUMP_FILE" ]]; then
    DUMP_PATH="$USER_DUMP_FILE"
    ensure_dump_path "$DUMP_PATH"
else
    DUMP_PATH="$(mktemp "${TMPDIR:-/tmp}/arthexis-db-migrate.XXXXXX.json")"
    if [[ $KEEP_DUMP -eq 0 ]]; then
        CLEANUP_DUMP=1
    fi
fi

cleanup() {
    if [[ $CLEANUP_DUMP -eq 1 ]] && [[ -n "${DUMP_PATH:-}" ]] && [[ -f "$DUMP_PATH" ]]; then
        rm -f "$DUMP_PATH"
    fi
}
trap cleanup EXIT

info "Creating schema on $DEST database"
CURRENT_DB_CONTEXT="destination"
run_manage "$DEST" migrate --noinput

if [[ "$DEST" == "postgres" && $FORCE -eq 1 ]]; then
    info "Clearing existing data on PostgreSQL destination"
    CURRENT_DB_CONTEXT="destination"
    run_manage "$DEST" flush --noinput
fi

info "Exporting data from $SOURCE database"
CURRENT_DB_CONTEXT="source"
run_manage "$SOURCE" dumpdata --natural-foreign --natural-primary --exclude=contenttypes --exclude=auth.Permission --indent 2 > "$DUMP_PATH"

info "Importing data into $DEST database"
CURRENT_DB_CONTEXT="destination"
run_manage "$DEST" loaddata "$DUMP_PATH"

if [[ $CLEANUP_DUMP -eq 1 ]]; then
    info "Temporary dump file removed"
else
    info "Database dump saved to $DUMP_PATH"
fi

info "Database migration from $SOURCE to $DEST completed successfully"
