# 1.0 Migration Trim Decision Gate

The 1.0 migration trim should happen once, after the remaining app-retirement
decisions are explicit. This note is the required checkpoint before replacing
the current historical migration graph with a short 1.0 fresh-install baseline.

## Decision Rule

Do not trim migrations until each local app is in one of these buckets:

1. Preserve as a 1.0 runtime app and collapse its migrations to the current
   schema state.
2. Preserve as an optional runtime app, but exclude it from default fresh
   profiles unless the profile or feature pack selects it.
3. Retire completely from fresh 1.0 installs and move any needed old-database
   recovery to explicit reconciliation or import commands.

The trim must not keep migration-only shims only because they were useful
during the cleanup wave. Those shims are temporary compatibility scaffolding
for pre-1.0 databases.

## Preserve

Preserve these areas as the default 1.0 core:

- Node identity, auth, admin, app registry, settings, modules, sites, locale,
  logs, media, release, services, users, and shared primitives.
- OCPP, charger state, RFID/cards, energy accounts, maps, protocols, and
  charger intake operations now folded into OCPP.
- Hardware and field-node support required by Control or Satellite profiles:
  discovery, imager, nmcli, rpiconnect, sensors, serialbridge, summary,
  and screens for dedicated Control/LCD hardware profiles.
- Operator and development surfaces that remain actively used: docs, reports,
  repos, skills, terminals, printers, clocks, and
  operational actions/APIs when selected by role or feature pack.
- Odoo as the CRM and office connector path. Do not retire Odoo in this trim.

Preserve only explicit node-to-node operations. Do not preserve OCPP/WebSocket
forwarding loops, always-on relay transports, or camera/audio streaming paths as
part of the 1.0 baseline.

## Retire From Fresh 1.0 Installs

Unless a later product decision reactivates one of them, these apps should not
survive in the fresh 1.0 migration graph:

- `apps.audio`
- `apps.awg`
- `apps.aws`
- `apps.chats`
- `apps.classification`
- `apps.deploy`
- `apps.embeds`
- `apps.flows`
- `apps.ftp`
- `apps.gdrive`
- `apps.library`
- `apps.meta`
- `apps.payments`
- `apps.playwright`
- `apps.projects`
- `apps.rates`
- `apps.shop`
- `apps.simulators`
- `apps.survey`
- `apps.teams`
- `apps.terms`
- `apps.vehicle`

Calendar-facing application surfaces and the legacy `netmesh` migration label
are already considered retired. Do not recreate them to preserve historical
migration continuity.

## Baseline Cut Result

This baseline cut applies the cleanup decisions that were previously blocking
the trim:

- The retired app list is excluded from fresh 1.0 app selection.
- Screen-device functionality is owned by the GWAY architecture, not the suite.
- Odoo is preserved as a baseline CRM connector because current energy, email,
  and task models hold Odoo relationships.
- Historical migration-only shim packages under `apps._legacy` are removed.
- Pre-1.0 database handling moves to the reinstall data import and
  reconciliation path.

## 1.0 Baseline Expectations

Fresh 1.0 installs should:

- install only preserved runtime apps plus explicitly selected optional apps;
- run a short migration plan for kept apps rather than replaying every
  pre-1.0 historical migration;
- exclude hard-retired app labels from `INSTALLED_APPS`, `MIGRATION_MODULES`,
  direct route providers, public route providers, Celery schedules, health
  checks, and root command wrappers;
- keep RFID/card/OCPP/energy import/export available without forwarding loops;
- keep Odoo installed as the CRM connector;
- keep screens only where the role or hardware profile requires them.

Pre-1.0 databases should use a reviewed reconciliation path instead of relying
on the fresh-install migration graph. That path may include SQL snapshots,
management-command exports, explicit imports for RFID/cards/energy/OCPP data,
and documented manual decisions for retired product apps.

## Standby Terminal Cutover Harness

Before the final migration trim, use the device-local standby Terminal helper to
exercise the cutover path without touching the normal Terminal checkout or its
`:8888` service:

```powershell
.\standby-terminal.bat start
.\standby-terminal.bat status
.\standby-terminal.bat upgrade --start
.\standby-terminal.bat validate-cutover
.\standby-terminal.bat stop
```

The helper creates an ignored isolated checkout under
`work/standby-terminal/checkout`, stores SQLite/log/cache runtime state under
`work/standby-terminal/runtime` and `work/standby-terminal/logs`, and serves the
standby instance on `http://127.0.0.1:8000/`. Use
`--state-dir <path>` when the standby checkout must live outside the active repo.

For a representative pre-trim database, stop the standby instance and pass a
database snapshot explicitly:

```powershell
.\standby-terminal.bat validate-cutover --source-db C:\path\to\pre-trim.sqlite3
```

Validation reports are written to `work/standby-terminal/reports/` and must be
kept as release-readiness evidence before opening the final migration trim PR.

## Verification

Run these checks after the baseline cut:

```bash
.venv/bin/python manage.py migrations check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py showmigrations --plan --skip-checks
.venv/bin/python manage.py check
.venv/bin/python manage.py test run -- apps/app apps/core apps/nodes apps/cards apps/ocpp apps/protocols
git diff --check
```

Also run role-profile checks that prove deprecated apps do not return to
default Watchtower, Satellite, or Terminal profiles, and that Control keeps
screens only through the expected hardware path.
