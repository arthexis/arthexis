# Reference attachment staged migration

Arthexis is moving from direct model-level `reference` fields to the generic
`links.ReferenceAttachment` model so references can be attached in a unified
way across apps.

To avoid breaking existing admin forms, integrations, and templates, use this
staged path.

## Stage 1: mirror writes, keep legacy fields

- Keep legacy `reference` fields on models.
- Call `mirror_legacy_reference_attachment(instance)` after save when the
  model exposes a legacy `reference` field.
- Mirror the legacy value into a primary slot on `ReferenceAttachment` using a
  stable slot name (`term`, `rfid`, `charger`, or `default`).

This stage is now wired for:

- `terms.Term`
- `cards.RFID`
- `ocpp.Charger`

## Stage 2: read through unified helpers

For UI and integration reads, switch to helpers in
`apps.links.reference_utils`:

- `get_attached_references(instance)`
- `get_primary_reference(instance)`

These functions prefer attachment-backed reads and gracefully fall back to the
legacy `reference` field when no attachment is present.

## Stage 3: remove legacy fields

After all read/write paths have been migrated and data backfills are complete,
legacy `reference` fields can be removed in follow-up schema migrations.

Until then, keep both paths in place to preserve admin and template
compatibility.
