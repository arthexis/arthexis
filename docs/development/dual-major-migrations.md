# Dual major migration tracks

Arthexis now supports two migration tracks in parallel:

- **Current line (`0.x`)** keeps appending migrations in each app's default
  `migrations/` module.
- **Next major line (`1.0`)** is rebuilt from scratch into per-app
  `migrations_v1_0/` modules.

This lets the suite keep shipping incremental upgrades for the current major
line while preparing a compressed, clean migration base for the next major.

## Tracking file

The repository stores migration-track state in `MIGRATIONS.json` at the
project root. It records the current version/line and the active next-major
branch module suffix.

## Rebuild the next-major branch

Run:

```bash
.venv/bin/python manage.py migrations next-major-rebuild --major-version 1.0
```

Behavior:

1. Keeps existing `apps/*/migrations/` files untouched.
2. Clears `apps/*/migrations_v1_0/` files except `__init__.py`.
3. Regenerates migrations into `migrations_v1_0` via `MIGRATION_MODULES`.
4. Tags each regenerated initial migration with `BranchTagOperation` using
   `major-1.0-base`.
5. Updates `MIGRATIONS.json`.

## Upgrade policy

When `1.0` ships:

1. Mark the final `0.x` migration as the deprecation bridge endpoint.
2. Switch runtime migration modules to `migrations_v1_0` as the active line.
3. Start a new next-major rebuild line (for example, `2.0`) in parallel.

This preserves one-major-at-a-time upgrade paths while keeping each major base
clean and compressed.
