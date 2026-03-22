# Deferred migration audit (high-volume apps)

## Rollout priority: first ten heavy transforms

The initial rollout targets the heaviest data-only transforms that either scan
entire tables or update large swaths of rows while remaining safe to execute
after schema apply. The first ten transforms are:

| Priority | Migration | Operation | Deferred transform |
| --- | --- | --- | --- |
| 1 | `nodes.0005_remove_seed_nodes` | `remove_seed_nodes` | `nodes.legacy_data_cleanup` |
| 2 | `nodes.0009_remove_arthexis_self_node` | `remove_arthexis_self_node` | `nodes.legacy_data_cleanup` |
| 3 | `nodes.0010_noderole_acronym` | `assign_acronyms` | `nodes.legacy_data_cleanup` |
| 4 | `modules.0003_normalize_module_paths` | `forwards` | `modules.normalize_paths` |
| 5 | `video.0006_videodevice_name_slug` | `populate_videodevice_names` | `video.populate_device_names` |
| 6 | `video.0008_videodevice_default_name_base` | `update_videodevice_default_name` | `video.normalize_base_device_name` |
| 7 | `reports.0004_named_reports_and_legacy_archive` | `archive_sql_reports` | `reports.archive_sql_reports` |
| 8 | `reports.0004_named_reports_and_legacy_archive` | `copy_product_metadata` | `reports.archive_sql_report_products` |
| 9 | `ocpp.0008_forwarder_defaults_and_exports` | `enable_forwarders_and_exports` | `ocpp.enable_forwarders_and_exports` |
| 10 | `ocpp.0024_chargingstation_charger_charging_station` | `_link_existing_charge_points` | `ocpp.link_charging_stations` |

Each of these transforms is idempotent, checkpointed, and safe to resume after
interruption through the release transform pipeline or the existing nodes
checkpoint model.

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

The remaining rollout targets execute through `release run-data-transforms`,
using persisted checkpoints under `.release-transforms/` so operators can rerun
bounded batches safely after deployment.
