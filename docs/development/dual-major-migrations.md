# Migration baseline reset workflow

The suite no longer maintains dual migration tracks. Migration maintenance now
uses a single canonical `apps/*/migrations/` graph.

## Baseline reset

Run a full baseline rebuild with:

```bash
.venv/bin/python manage.py migrations rebuild
```

Behavior:

1. Clears `apps/*/migrations/` files except `__init__.py`.
2. Regenerates migrations into the canonical `migrations/` modules.
3. Produces fresh initial migrations without branch-tag markers or parallel
   module routing.

## Policy

- Do not create `migrations_v*` module trees.
- Do not use branch-tag migration operations for baseline orchestration.
- Keep migration history compact by regenerating a clean baseline when
  architecture freeze planning calls for it.

## Runtime reconciliation compatibility note

Baseline reset policy controls how the migration graph is maintained in-repo.
It does **not** remove runtime SQLite reconciliation support used by
`env-refresh.py` recovery guidance (`./upgrade.sh --migrate`) when a
graph/version mismatch is detected.
