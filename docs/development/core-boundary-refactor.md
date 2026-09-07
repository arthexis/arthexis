# `apps.core` boundary refactor

This document defines the target architecture for the staged `apps.core` refactor. The work is intentionally performed one reviewed step at a time on a single long-lived pull request.

## Target invariant

`apps.core` owns only Django/runtime infrastructure that is shared by every Arthexis deployment and that does not belong to a product or integration domain.

In particular, `apps.core` must not be the owner of email, release/upgrade, RFID/cards, Odoo, analytics, public-site lead capture, user/account, reporting, repository/GitHub, node, or operator-workflow behavior.

The intended dependency direction is:

```text
apps.base -> apps.core -> domain apps
```

`apps.base` contains the lowest-level reusable model/framework primitives. `apps.core` may depend on those primitives, but neither `apps.base` nor `apps.core` should depend on product domains once this refactor is complete.

## Model ownership target

| Current core concept | Target owner | Notes |
| --- | --- | --- |
| `EmailArtifact` | `apps.emails` | Email persistence belongs with collectors/inboxes/outboxes. |
| `EmailTransaction` | `apps.emails` | Move with its attachments and admin/services. |
| `EmailTransactionAttachment` | `apps.emails` | Move with `EmailTransaction`. |
| `AdminNotice` | `apps.ops` | Generic operator notice; release-specific permissions move to `apps.release`. |
| `UsageEvent` | `apps.analytics` | New app for cross-application usage collection and reporting. |
| `InviteLead` | `apps.sites` | Public-site invitation/acquisition concern. |
| `LeadBase` | `apps.sites` or remove | Inline into `InviteLead` if no other concrete consumers remain. |
| `Ownable` | `apps.base` | Cross-domain ownership primitive. |
| owned-object presentation helpers | `apps.base` admin helpers | Keep admin URL/link rendering out of the model primitive. |
| `SecurityGroup` compatibility re-export | remove | Import from `apps.groups` directly. |
| `apps.core.entity` compatibility re-export | remove | Import from `apps.base.models` directly. |

## Module ownership target

The following current `apps.core` areas are migration candidates rather than permanent core responsibilities:

- email polling, email admin and email task wiring -> `apps.emails`
- app-registry/profile selection checks and commands -> `apps.app`
- auto-upgrade, changelog, version/release publishing and upgrade UI -> `apps.release`
- Odoo views/admin/routes -> `apps.odoo`
- RFID/card views/admin/routes -> `apps.cards`
- usage analytics collection, model and UI -> `apps.analytics`
- invitation/lead handling -> `apps.sites`
- user/account health and temporary-password/impersonation behavior -> `apps.users`
- operator interrupts/notices/workflows -> `apps.ops`
- report rendering/report views -> `apps.reports`
- GitHub health issue reporting -> `apps.repos`

Health checks should ultimately be registered by the app that owns the checked capability. A small common registry may remain in `core` only if it is genuinely domain-independent.

## Migration safety rules

Model ownership, Django migration state, content types, Celery task identifiers, and physical table names must be treated as separate concerns.

1. Move Python/Django ownership first while preserving the existing physical table with `Meta.db_table` and explicit migration state operations where necessary.
2. Preserve and migrate existing `ContentType` rows rather than silently creating parallel content types under new app labels.
3. Rewrite persisted Celery task paths and keep compatibility callables for one transition window where needed.
4. Rename physical tables only in a later migration after the new ownership has shipped successfully.
5. Preserve public URL names or add compatibility redirects where external links may exist.

This makes each stage rollbackable and keeps application-boundary failures separate from schema-renaming failures.

## Staged sequence

The pull request should progress in these review gates:

1. **Boundary contract and guardrails** — this step. Document the target, freeze growth of legacy core model exports and manifest dependencies, and make the remaining debt explicit.
2. **Foundation primitives** — move `Ownable` and generic seed-data helpers out of core; remove inverse `base -> groups` bootstrap coupling.
3. **Email ownership** — move core email models/admin/tasks to `apps.emails`, including the existing nodes/email admin boundary debt.
4. **Application registry** — move app registry/profile checks and commands to `apps.app`.
5. **Release/upgrade** — consolidate auto-upgrade, release, changelog and upgrade UI/services under `apps.release`.
6. **Domain UI extraction** — move Odoo, RFID, user, report and analytics views/routes/admin to their owners.
7. **Remaining core models** — move `UsageEvent`, `AdminNotice`, and `InviteLead` with explicit migration/content-type handling.
8. **Health/operator services** — make health checks domain-owned and move operator/GitHub reporting adapters to `ops`/`repos`.
9. **Slim `CoreConfig.ready()`** — move domain-specific startup behavior to owning apps.
10. **Final import-linter enforcement** — prohibit `apps.core` and `apps.base` from depending on product domains and remove transition allowlists.

## Step 1 guardrail semantics

During the staged refactor, existing legacy dependencies must be allowed to shrink without requiring the guardrail to be rewritten after every move. New legacy surface must not be added.

The accompanying architecture test therefore treats today's exported core model names and manifest `REQUIRES_APPS` values as a **maximum legacy set**:

- removing an entry is allowed and expected;
- adding a new entry fails the test;
- the final step replaces these temporary shrink-only guards with strict import-linter boundaries.

This test is deliberately narrow. It does not pretend the final dependency rule is already true while the refactor is still in progress.
