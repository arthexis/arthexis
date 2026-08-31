# Entity Adoption Rubric

This rubric defines when a Django model should inherit `apps.base.models.Entity`.

## Intent

Use `Entity` for models that represent complete business entities with an independent lifecycle.
Do not force `Entity` onto dependent record types that only exist to support another entity.

## Decision Buckets

Each concrete model must be classified in one bucket:

- **adopt_now**: model should inherit `Entity` in the current migration wave.
- **adopt_later**: model likely qualifies but needs follow-up due to compatibility risk.
- **do_not_adopt**: model should remain a plain `models.Model` because it is dependent or append-only.

## Qualification Signals

A model is usually a complete entity when most of these are true:

- It has a recognizable business identity.
- It can be managed directly in admin and policy workflows.
- It has an independent lifecycle and status transitions.
- Other models depend on it, not only the reverse.

A model is usually not a complete entity when one or more of these dominate:

- It is an append-only event/log/history/snapshot record.
- It is a child/value row that cannot exist meaningfully on its own.
- It is primarily a join/through/parameter table.

## Compatibility Checklist (Required before adoption)

Adding `Entity` changes behavior:

- `objects` filters out `is_deleted=True` rows.
- `all_objects` includes all rows.
- Delete behavior may become soft delete for seed data.

Before changing a model, review:

1. Query assumptions (`objects` vs `all_objects`).
2. Delete semantics in services, admin actions, and commands.
3. Unique-constraint behavior with soft-deleted rows.
4. Integration/export expectations that may rely on hard delete.

## Inventory Command

Use the scorecard command to generate the first-pass model inventory:

```bash
.venv/bin/python manage.py entity_audit
.venv/bin/python manage.py entity_audit --format json
```

Treat command output as heuristic guidance, then confirm per model during each migration wave.

## Next Steps After Inventory (No Migration Yet)

### Step 3: Compatibility Impact Scan

For each candidate model in `adopt_now` and `adopt_later`, capture compatibility notes before any inheritance change:

- QuerySet behavior risk (`objects` filtering after soft delete)
- Delete behavior risk (seed-data soft delete vs hard delete)
- Uniqueness risk with soft-deleted rows
- Admin/workflow and integration/export assumptions

Use targeted repository scans per model label (examples):

```bash
rg -n "<ModelName>|<model_name>|\.delete\(|update_fields=|all_objects|objects" apps
```

Record findings in the adoption matrix under the model's planning notes.

### Step 4: Wave Planning

Group models into migration waves without changing code yet:

- **Wave 1 (low risk)**: `adopt_now` + compatibility risk `low`
- **Wave 2 (medium risk)**: `adopt_now` + compatibility risk `medium`
- **Wave 3 (high risk/complex)**: remaining `adopt_now` and validated `adopt_later`

No model should be moved into an implementation wave until Step 3 notes exist for that model.
