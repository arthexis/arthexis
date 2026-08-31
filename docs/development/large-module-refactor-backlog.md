# Large Module Refactor Backlog

Snapshot source: `origin/main` after fetch on May 31, 2026.

The scan excluded migrations, virtual environments, generated caches, work
directories, and dependency folders. The first follow-ups split release
publishing progress helpers and CSMS charging-profile helpers into package-local
modules while preserving their public import surfaces.

## Completed Package Splits

| Module | Result |
| --- | --- |
| `apps/release/publishing/pipeline/actions.py` | Progress screen state and guidance helpers moved into `apps.release.publishing.pipeline.progress`. |
| `apps/ocpp/consumers/csms/consumer.py` | Charging-profile action helpers moved into `apps.ocpp.consumers.csms.handlers.charging_profiles`. |
| `apps/ocpp/call_result_handlers/legacy.py` | Converted into the `apps.ocpp.call_result_handlers.legacy` package and moved certificate result handlers into `legacy.certificates`. |

## Follow-up Candidates

| Module | Lines | Suggested direction |
| --- | ---: | --- |
| `apps/ocpp/consumers/csms/consumer.py` | 1,970 | Continue splitting protocol message routing, connection lifecycle, and admin/event side effects behind the existing consumer class. |
| `apps/imager/services/build_engine.py` | 1,503 | Move build phase orchestration, artifact inspection, and host command execution into smaller service modules under the existing package. |
| `apps/ocpp/call_result_handlers/legacy/__init__.py` | 1,490 | Continue moving remaining legacy handler domains into package-local modules while keeping `legacy.handle_*` compatibility. |
| `apps/ocpp/admin/miscellaneous/core_admin.py` | 1,479 | Split admin actions, display helpers, and queryset/form wiring into focused admin support modules. |
| `apps/nodes/admin/node_admin.py` | 1,409 | Extract node display sections, bulk actions, and health/status helpers while keeping admin registration stable. |
| `apps/nodes/management/commands/node.py` | 1,354 | Move subcommand handlers and environment inspection helpers into command support modules. |
| `apps/repos/github_monitor.py` | 1,346 | Separate GitHub API polling, local state reconciliation, and notification/report formatting. |
| `apps/sigils/sigil_resolver.py` | 1,294 | Split parsing, lookup, permission checks, and resolution formatting into resolver package modules. |
| `apps/core/tasks/auto_upgrade/tasks.py` | 1,261 | Separate scheduling, environment refresh, command execution, and reporting concerns. |
| `scripts/ap_portal_server.py` | 1,205 | Move firewall, DNS, HTTP request handling, and registration notification code into support modules if the script continues to grow. |
| `env-refresh.py` | 1,193 | Extract platform detection, dependency refresh, command execution, and reporting into an importable support package. |
| `apps/docs/views.py` | 1,180 | Split view routing, page discovery, rendering context, and search/navigation helpers. |
| `apps/skills/package_services.py` | 1,049 | Split scan, export, import, materialization, and validation services into the existing skills package. |

## Guardrails

- Keep each split compatibility-preserving unless the follow-up issue explicitly
  approves an import cleanup.
- Prefer package-local modules and re-export shims over broad call-site churn.
- Add focused structure tests when a refactor creates new compatibility modules.
- Run the nearest Django test targets plus `manage.py check --fail-level ERROR`
  before moving each follow-up PR out of draft.
