# Deferred migration audit (high-volume apps)

## apps/nodes

| Migration | Operation | Classification | Rationale |
| --- | --- | --- | --- |
| `0002_squashed_0011_netmessage_expires_at` | `remove_invalid_clipboard_tasks` | schema-coupled | Keeps schedule rows consistent when clipboard task was removed; low-volume and safe during migrate. |
| `0002_squashed_0011_netmessage_expires_at` | `remove_seed_nodes` | deferrable | Deletes potentially large node sets and is not required for schema validity. |
| `0002_squashed_0011_netmessage_expires_at` | `remove_arthexis_self_node` | deferrable | Data cleanup only; no dependency for schema transition. |
| `0002_squashed_0011_netmessage_expires_at` | `assign_acronyms` | deferrable | Backfill transform only; column creation remains schema-critical. |

## apps/content

| Migration | Operation | Classification | Rationale |
| --- | --- | --- | --- |
| `0005_webrequestsampler_ownable` | `remove_conflicting_webrequestsampler_owners` | schema-coupled | Must run before exclusivity constraint enforcement to avoid migration failure. |
| `0007_alter_contentclassifier_options_and_more` | `backfill_translations` | schema-coupled | Required before source columns are removed in same migration. |

## Post-upgrade execution model

Deferrable `apps/nodes` transforms are executed by the idempotent Celery task
`nodes.tasks.run_deferred_node_migrations`, with checkpoints persisted in
`nodes_nodemigrationcheckpoint` and operator visibility via:

- Admin: `Node migration checkpoints`
- API status: `GET /nodes/migration-status/`
