# SQLite "database is locked" errors on control nodes

SQLite is the default database backend on control nodes. It is reliable for low
throughput, but the single-writer constraint means concurrent writes can cause
`OperationalError: database is locked` when multiple processes hold the database
file at once.

## Common causes
- **Concurrent services writing simultaneously**: Multiple Django processes
  (e.g., gunicorn workers, Celery beat/worker, upgrade scripts, or background
  threads like RFID readers) can try to write at the same time. SQLite allows
  only one writer, so overlapping transactions can block others until the lock
  timeout elapses.
- **Long-running transactions**: Tasks that keep a transaction open—bulk updates,
  fixture loads during upgrades, or migrations—can hold the write lock long
  enough that other processes fail when their timeout is reached.
- **Service restarts during maintenance**: When `env-refresh.py` or upgrade
  scripts reload fixtures, parallel services that remain running (web workers or
  Celery) may keep querying the database. That cross-traffic increases the odds
  of a lock during the maintenance window.
- **External access to the DB file**: Manual use of `sqlite3`, backup tooling,
  or monitoring agents that open the database directly can hold locks that block
  Django.

## What to check
- **Align service orchestration**: During upgrades or env refreshes, stop or
  pause web workers and Celery so only the maintenance process uses the DB.
- **Review Celery beat tasks**: Periodic tasks (such as `nodes.Node` feature
  sync) can write while pages are rendering; stagger schedules or reduce worker
  count to limit contention.
- **Inspect long transactions**: Look for migrations or management commands that
  hold the lock; ensure they commit promptly and avoid wrapping heavy loops in a
  single transaction when using SQLite.
- **File-system considerations**: SQLite expects a local POSIX filesystem. If
  the DB lives on a network or shared volume, locking can be unreliable; move it
  to local storage on control nodes.

## Mitigations
- Minimize concurrent writers (single gunicorn worker, short-lived Celery worker
  pool) on SQLite-based control nodes.
- Run upgrades and fixture loads with other services stopped, then restart them
  afterward.
- If sustained concurrency is required, migrate the deployment to Postgres so
  multiple writers can operate without hitting SQLite's single-writer limit.
