# Docker Compose role preset profiles

The repository now includes a `compose.yaml` that maps Arthexis role presets to
Compose profiles.

## Service map by profile

- `terminal`: `web` only.
- `control`: `web` + `redis` + `nginx`.
- `satellite`: `web` + `redis` + `nginx`.
- `watchtower`: `web` + `redis` + `nginx`.

`web` always receives role-related environment variables so role validation can
be satisfied without editing the Compose file:

- `ARTHEXIS_ROLE_PRESET`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `OCPP_STATE_REDIS_URL`
- `CHANNEL_REDIS_URL`
- `BROKER_URL`

Set `ARTHEXIS_ROLE_PRESET` to match the profile before startup (shell export or
`.env` file), for example `control`, `satellite`, `watchtower`, or `terminal`.

## One-command startup examples

After setting `ARTHEXIS_ROLE_PRESET` appropriately:

- `docker compose --profile control up -d`
- `docker compose --profile terminal up`

## Healthchecks

- `web` healthcheck performs an HTTP request against `http://127.0.0.1:8888/`.
- `redis` healthcheck runs `redis-cli ping`.

## Persistence strategy

Compose uses named volumes so container restarts do not lose state:

- `arthexis_db` mounts at `/app/data` and stores SQLite DB data via
  `ARTHEXIS_SQLITE_PATH=/app/data/db.sqlite3`.
- `arthexis_media` mounts at `/app/media` for uploaded/generated media.
- `redis_data` mounts at `/data` for Redis persistence in non-terminal roles.

For local development, remove data with `docker compose down -v` only when you
explicitly want a fresh database/media/redis state.

## SQLite driver selection

For SQLite-backed environments, you can select the Python SQLite driver with:

- `ARTHEXIS_SQLITE_DRIVER=stdlib` (default)
- `ARTHEXIS_SQLITE_DRIVER=pysqlite3` (requires separate installation of `pysqlite3-binary` on Linux)

Example:

```bash
ARTHEXIS_SQLITE_DRIVER=pysqlite3 python manage.py shell -c "import sqlite3; print(sqlite3.sqlite_version)"
```

If `ARTHEXIS_SQLITE_DRIVER=pysqlite3` is set but the package is unavailable,
Arthexis falls back to the standard-library `sqlite3` module and emits a warning.
