# Reinstall + data import runbook (1.0+)

Use this runbook when an environment was created from a legacy migration path.
Arthexis `1.0+` supports **fresh install + import** as the upgrade path for those
legacy databases.

## 0) Architecture freeze quick context

Latest planning baseline (from recent release prep commits):

- 1.0 architecture freeze landed as a migration-baseline reset with no dual-track
  migration support.
- Runtime legacy migration shims were removed.
- 1.0 release prep work focused on fresh-install/import operations instead of
  in-place migration from old graphs.
- Recent PRs in this sequence included `#6575` (1.0 baseline decision record),
  `#6576` (single migration baseline), and `#6579` (legacy shim removal).

Before you start, confirm goals for the environment:

1. Fresh database bootstrap succeeds.
2. Startup checks and OCPP smoke checks pass.
3. Data import succeeds from approved export artifacts.
4. Operator docs match actual script behavior.

Initial validation steps completed on **2026-03-28**:

- Fresh SQLite migration run completed successfully (`manage.py migrate --noinput`).
- Startup preflight completed successfully (`run_runserver_preflight`).
- Core and OCPP grouped health checks passed (`manage.py health --group core --group ocpp --force`).
- OCPP smoke tests passed (`test_chargers_command.py`, `test_simulator_command.py`).

## 1) Backup and export from old environment

From the old node, capture at least:

- `db.sqlite3` (or the DB dump used in your deployment model).
- `data/*.json` export fixtures if you already use fixture-based import.
- `.env`, `redis.env`, and node lock files under `.locks/` that you need to
  re-apply deliberately after reinstall.

Do **not** copy old migration files into the new checkout.

## 2) Reinstall on a fresh database

```bash
./install.sh --clean --no-start
```

If you need service/role options, include them in the same reinstall command
(for example `--service`, `--terminal`, `--control`, `--systemd`, etc.).

### Optional: `upgrade.sh --migrate` reconciliation mode

If you are performing a major-version refresh and need best-effort row carryover:

- On **SQLite**, `--migrate` snapshots `db.sqlite3` into `.locks/*.pre_major_migrate.sqlite3`, rebuilds the schema, then copies compatible rows with `INSERT OR IGNORE`.
- On **PostgreSQL**, `--migrate` snapshots the active DB into `.locks/*.pre_major_migrate.dump` using `pg_dump`, rebuilds the schema, restores that dump into a temporary DB (`arthexis_pre_major_migrate_snapshot`), then copies compatible rows using conflict-tolerant inserts.
- Other database backends are not supported by `--migrate` and fail fast.

Review reconciliation output after the run; it reports copied tables plus skipped tables/columns/rows for auditability.

### Optional: safe fallback `--reconcile`

For upgrades where you want automatic rescue for migration graph/version
mismatches:

```bash
./upgrade.sh --reconcile
```

When enabled, if `env-refresh.py` detects mismatch failures (for example
`InvalidBasesError` or branch splinter/tag conflicts), it logs:

1. mismatch detected,
2. fallback engaged,
3. reconciliation summary (`copied=`, `skipped=`, `missing=`).

Without this flag, upgrade remains fail-fast for mismatch failures, and operators
must rerun with explicit reconciliation (for example `./upgrade.sh --migrate`).

#### Recovery steps after fallback (forward-only policy)

1. Stop services (`./upgrade.sh --stop`) before restoration.
2. Restore the database from your pre-upgrade DB backup/snapshot (or from the
   generated `.locks/*.pre_major_migrate.*` artifact if that is your approved
   recovery source).
3. Restore the repository working tree to the matching pre-upgrade revision
   (for example with your Git tag/commit pinning process for that environment).
4. Re-run upgrade with the --local flag and inspect migration health before continuing.

Do not perform reverse migrations as an operational recovery path; Arthexis
upgrade recovery is database + repository restore based.

#### Post-check steps after fallback

Run the same minimum gates as any reinstall/import flow:

```bash
.venv/bin/python manage.py migrate --noinput
bash -lc 'source scripts/helpers/runserver_preflight.sh && run_runserver_preflight'
.venv/bin/python manage.py health --group core --group ocpp --force
.venv/bin/python manage.py test run -- apps/ocpp/tests/test_chargers_command.py apps/ocpp/tests/test_simulator_command.py
```

### Upgrade troubleshooting: in-progress merge/rebase state

If `upgrade.sh` is interrupted and Git reports an unfinished merge/rebase,
prefer rerunning the same upgrade command first. Control and Watchtower upgrade
flows already attempt to abort incomplete Git operations and realign to
`origin/<branch>` before pulling.

Use manual Git recovery only for niche/operator workflows (for example, Terminal
nodes or direct Git operations outside `upgrade.sh`):

1. Check the active Git operation and conflicted files:
   ```bash
   git status
   ```
2. Either complete conflict resolution (`git add ...` + `git rebase --continue`
   / `git merge --continue`) or abort the active operation (`git rebase --abort`
   / `git merge --abort`).
3. After `git status` is clean, switch back to the tracked branch:
   ```bash
   git switch <branch>
   ```
4. Rerun the intended upgrade command.

## 3) Import approved data payloads

If your import package is Django fixtures:

```bash
.venv/bin/python manage.py loaddata data/*.json
```

If you have additional app-specific import commands, run them now.

## 4) Smoke checks

Run the minimum release gate checks:

```bash
.venv/bin/python manage.py migrate --noinput
bash -lc 'source scripts/helpers/runserver_preflight.sh && run_runserver_preflight'
.venv/bin/python manage.py health --group core --group ocpp --force
.venv/bin/python manage.py test run -- apps/ocpp/tests/test_chargers_command.py apps/ocpp/tests/test_simulator_command.py
```

## 4.1) Deferred data-transform follow-up (when required)

If your release/import flow includes deferred data cleanup or backfill work,
run the canonical release transform command after schema migration:

```bash
./command.sh run_release_data_transforms
```

This command is an ops alias for `release run-data-transforms` and can be
rerun safely because transform progress is checkpointed.

For node-specific deferred migration progress, use operator-facing status
surfaces:

- Admin: `Node migration checkpoints`
- API: `GET /nodes/migration-status/`

## 5) Release gate for tag/publish

Only tag/publish after all checks above pass and docs reflect current behavior:

```bash
git tag -a 1.0.0 -m "Release 1.0.0"
git push origin 1.0.0
```

If any check fails, stop and fix before tagging.

## Fail-fast behavior reference

`install.sh` and `upgrade.sh` run a migration-history guard against the active
default database. When unknown legacy migration entries are found, scripts abort
with a clear message and point to this runbook.
