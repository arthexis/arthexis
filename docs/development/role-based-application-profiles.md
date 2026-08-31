# Role-Based Application Profiles

Arthexis currently installs nearly every local Django app on every node. That
keeps startup simple, but it also forces every install to run migrations for
apps that the node does not use. The first goal of role-based application
profiles is to keep Watchtower, Control, Satellite, and Terminal nodes on a
smaller `INSTALLED_APPS` set that matches what they actually run.

The profile system should make application loading a boot-time decision, not a
runtime visibility-only setting. Feature flags, modules, and application rows
can still control what users see after boot, but disabled Django apps should not
be imported and should not participate in migration checks.

This page documents the rollout context from
[issue #8336](https://github.com/arthexis/arthexis/issues/8336) and the
documentation step tracked by
[issue #8349](https://github.com/arthexis/arthexis/issues/8349). Later rollout
issues should treat the app sets here as the design target unless a dependency
audit proves that a listed app must move between a default role set and an
optional feature pack.

## Current State

`config/settings/apps.py` resolves `INSTALLED_APPS` from the role profile,
feature packs, local disables, and `.locks/enabled_apps.lck`. The lock is the
boot-time contract for which Django apps participate in startup and migrations.

Route providers, middleware entries, ASGI routing, Celery schedules, and
startup health checks are guarded so disabled or removed apps do not need to be
imported at boot.

The local production suite on this node uses Watchtower roles for all three
bound instances:

- `/home/ubuntu/arthexis`
- `/home/ubuntu/audi`
- `/home/ubuntu/porsche`

## Default App Set Summary

The proposed default set is the union of the all-node baseline and exactly one
role-specific default set. Watchtower, Satellite, and Terminal keep narrow
segregated defaults, and optional feature packs are separate opt-ins for those
roles. Control is the exception: it is the operator/control-plane role and
receives every manifest-declared local app by default except selectors marked
feature-pack-only in `_control_app_selectors()` so new default-on apps are
available there without adding a separate Control selector.

| Set | Default apps |
| --- | --- |
| All nodes | `apps.app`, `apps.base`, `apps.celery`, `apps.core`, `apps.credentials`, `apps.counters`, `apps.features`, `apps.groups`, `apps.locale`, `apps.locals`, `apps.media`, `apps.modules`, `apps.odoo`, `apps.ocpp`, `apps.release`, `apps.services`, `apps.sigils`, `apps.sites`, `apps.totp`, `apps.users`, `apps.whitenoise` |
| Watchtower | `apps.actions`, `apps.certs`, `apps.docs`, `apps.dns`, `apps.emails`, `apps.nginx`, `apps.ops`, `apps.protocols`, `apps.reports`, `apps.repos` |
| Control | All manifest-declared local Django apps except feature-pack-only selectors such as `apps.shop` |
| Satellite | `apps.discovery`, `apps.nmcli`, `apps.ocpp`, `apps.protocols`, `apps.sensors`, `apps.serialbridge` |
| Terminal | `apps.docs`, `apps.imager`, `apps.repos`, `apps.skills`, `apps.terminals` |

For this environment, the hosted `arthexis.com`, `audi`, and `porsche`
instances should use the Watchtower default set plus only the explicit feature
packs needed by each instance. Hosted OCPP, shop/public storefronts, customer
billing, CRM, cloud helpers, feedback/chat, and public widgets are feature-pack
choices, not Watchtower defaults.

## Profile Inputs

Application loading should be resolved from these inputs, in order:

1. Platform core apps that are required on every Arthexis node.
2. The node role from `NODE_ROLE` or `.locks/role.lck`.
3. Optional feature packs enabled by environment, lock file, or node config.
4. Explicit local disables for apps that a role would normally include.
5. Dependency closure from each app manifest's `REQUIRES_APPS`.

Dependency closure should be a validation guard, not a way for optional
hardware apps to leak into every role. When a baseline candidate currently
requires role-specific apps, the implementation must split that manifest or
leave the candidate out of the baseline set until the dependency is resolved.

The resolved set should be written to `.locks/enabled_apps.lck` before restart
and read by settings during boot. If the lock is missing, setup and recovery
commands may use a conservative full-app fallback until the node role has been
resolved.

## Enabled Apps Lock Tooling

Operators can inspect the resolved app set before writing the boot lock:

```bash
python manage.py enabled_apps_lock --role Watchtower
```

The command prints each enabled selector with the reason it was selected:
all-node baseline, role default, feature pack, explicit include, dependency
closure, or full-app setup/recovery fallback when the role is unknown.

Feature packs, explicit includes, and local disables can be layered onto the
role profile:

```bash
python manage.py enabled_apps_lock \
  --role Watchtower \
  --feature-pack hosted-ocpp \
  --feature-pack screen-devices \
  --include apps.screens \
  --disable repos
```

`--disable` rejects baseline Django/platform apps such as
`django.contrib.admin` because `.locks/enabled_apps.lck` only persists the
profile-managed app set. If a baseline app must be disabled at boot, keep that
disable in `ARTHEXIS_ROLE_APP_DISABLED_APPS` or `ARTHEXIS_DISABLED_APPS`.
The shared validator also treats `apps.core` as required because recovery
commands and lock rendering depend on it. Disables that indirectly prune a
required app through dependency availability are rejected before the lock is
written.

Writing `.locks/enabled_apps.lck` is opt-in:

```bash
python manage.py enabled_apps_lock --role Watchtower --write
```

Use `--json` for automation. Use `--strict` when setup tooling must fail on an
unknown role instead of preserving the full-app fallback.

The command never drops tables or deletes app data. Existing tables for disabled
apps remain in the database until an operator runs a separate explicit
destructive cleanup command.

## Benchmark Expectations and Procedure

Role profile benchmarks should measure install and upgrade cost on the same
commit before and after profile changes whenever possible. The primary
expectation is that Satellite benefits the most: it should have the largest
install/upgrade cost reduction and, after refinement, the lowest role-profile
install/upgrade cost among Terminal, Satellite, and Control.

If Satellite does not show the largest gain, treat that as a profile-quality
finding. Investigate whether dependency closure, optional feature packs,
migration participation, startup probes, or role defaults are still pulling
Control-only or Watchtower-only behavior into the Satellite profile. Raise a
follow-up issue with the measured evidence, likely app/profile leak, and the
tests or scripts needed to tighten the profile.

Use this benchmark matrix for profile rollout checks:

| Role | Node or host | Expected result | Notes |
| --- | --- | --- | --- |
| Terminal | Local Terminal checkout | Compact local-tooling profile with lower migration/startup cost than the previous full-app baseline. | Use a clean checkout so local operator work does not skew results. |
| Satellite | Local or field Satellite profile checkout | Largest improvement and lowest install/upgrade cost after profiles are refined. | Investigate immediately if Satellite is not the strongest beneficiary. |
| Control | `gway-001` | Higher cost than Satellite is acceptable because Control keeps hardware apps by default. | Prove GWAY reachability first and use suite-native upgrade/doctor flows. |

Benchmarks must use native suite entrypoints in clean local checkouts or
approved SSH targets that match the role/profile under test.
External container runtimes are not an accepted requirement for this workflow;
Raspberry Pi and similarly constrained nodes must use native suite entrypoints.

Record benchmark results locally with the commit SHA, role, node, command,
duration, status, blocker, and artifact path. Include blockers, skills, and
tools next to the results so follow-up work can be reproduced after a context
handoff. Required local context:

- Skills and runbooks: `$arthexis-node-upgrade-doctor` for install/upgrade
  validation, plus the active operator GWAY recovery runbook for gway-001 route
  and service checks.
- Tools: `scripts/benchmark-suite.sh`, `.venv/bin/python manage.py migrations
  benchmark`, `.venv/bin/python manage.py test benchmark`, and suite-native
  upgrade/install entrypoints.
- Blockers: dirty benchmark checkout, gway-001 route failure, missing physical
  Control hardware, profile changes not yet merged, or unavailable credentials
  for the target node.

Benchmark timers for known-slow install and upgrade stages should be generous.
Do not treat a slow run as a regression until the command's own heartbeat,
process state, and logs show that it is stalled rather than still progressing.

## All Nodes

These apps should be available on every regular Arthexis install because they
provide identity, admin, auth, lifecycle, routing, site migration compatibility,
and shared model primitives:

- `apps.actions`
- `apps.app`
- `apps.base`
- `apps.celery`
- `apps.core`
- `apps.credentials`
- `apps.counters`
- `apps.features`
- `apps.groups`
- `apps.locale`
- `apps.locals`
- `apps.media`
- `apps.modules`
- `apps.odoo`
- `apps.ocpp`
- `apps.release`
- `apps.services`
- `apps.sigils`
- `apps.sites`
- `apps.totp`
- `apps.users`
- `apps.whitenoise`

`apps.celery` is baseline only as an atomic pair with
`apps.celery.beat_app.CeleryBeatConfig`, which supplies the
`django_celery_beat` app. `apps/celery/models.py` imports
`django_celery_beat.models`, and `apps/celery/migrations/0001_initial.py`
depends on the `django_celery_beat` migration label, so selective settings must
include the beat companion whenever `apps.celery` is installed.

`apps.leads` has been removed from runtime code. New lead-capture behavior
should live in the owning app or in the Odoo connector path rather than
restoring a shared Leads app.

`apps.nodes` remains part of the OCPP dependency closure rather than the
all-node baseline. Its local hardware behavior must continue to be role and
feature guarded.

The following third-party and Django core apps should remain baseline until a
separate compatibility audit proves they can be removed:

- `channels`
- `django.contrib.admin`
- `django.contrib.auth`
- `django.contrib.contenttypes`
- `django.contrib.messages`
- `django.contrib.sessions`
- `django.contrib.sites`
- `django.contrib.staticfiles`
- `django_object_actions`
- `django_otp`
- `django_otp.plugins.otp_totp`
- `import_export`

`parler` should stay baseline while translated content models remain part of
core content workflows. If content becomes a fully optional pack, `parler` can
move with that pack.

## Watchtower Profile

Watchtower is for cloud, customer, public-site, orchestration, and hosted OCPP
nodes after the OCPP feature pack is explicitly enabled. `arthexis.com`, `audi`,
and `porsche` should use this profile in the current environment.

Default Watchtower apps:

- `apps.actions`
- `apps.certs`
- `apps.docs`
- `apps.dns`
- `apps.emails`
- `apps.nginx`
- `apps.ops`
- `apps.protocols`
- `apps.reports`
- `apps.repos`

Recommended Watchtower feature packs:

- Hosted OCPP: `apps.ocpp` plus the explicit OCPP dependency pack
  (`apps.nodes`, `apps.cards`, `apps.energy`, `apps.maps`, `apps.protocols`).
  FTP report handling is retired from hosted OCPP. The `file_transfer` feature
  pack selects no runtime app.
- Public commerce: no runtime app is selected by the feature pack.
- Customer energy/billing integrations: `apps.energy`.
- CRM and office integrations: `apps.odoo`; Google Drive is deprecated.
- CDN-backed public assets: retired.
- Charger migration cutovers: deprecated and unsupported. The
  `charger_cutovers`/`charger-cutovers` feature pack is rejected during app
  profile resolution so deployments cannot silently run charger-facing OCPP
  admission without the retired blue/green policy machinery.
- Cloud deployment helpers: deprecated; no app is selected by the feature pack.
- Raspberry Connect update builder: `apps.imager`, `apps.rpiconnect`
  (`rpi_connect_updates`)
- User feedback and chat bridges: retired.
- Public widgets: retired.

OCPP is a known dependency blocker for the Watchtower default. The runtime
manifest keeps OCPP paired with `apps.nodes`, `apps.energy`, `apps.maps`,
`apps.cards`, and `apps.protocols`. Historical migrations still rely on the FTP
migration label through the compatibility app, but runtime FTP is no longer part
of the hosted OCPP feature pack.

Watchtower should not enable hardware-probing apps by default. In particular,
RFID, LCD, USB inventory, serial bridge, and Raspberry Pi Connect behavior
should be opt-in feature packs.

RFID is a known dependency blocker for the Watchtower target. `apps.users`
currently stores `User.login_rfid` as a foreign key to `cards.RFID`, and
`config/settings/auth.py` includes `apps.users.backends.RFIDBackend`, whose
module imports `apps.cards` at import time. Before a Watchtower profile can omit
`apps.cards`, the implementation must decouple user/auth RFID support by moving
RFID login state into an installed-only cards-side extension or otherwise
guarding the relation and backend imports behind the RFID feature pack.

## Control Profile

Control is for operator/control-plane nodes, single-device testing, and
hardware-attached appliances. It installs manifest-declared local Django apps by
default except feature-pack-only apps such as `apps.shop`, while still honoring
explicit local disables and dependency validation. This keeps new default-on
suite apps available on Control immediately after upgrade without adding another
hand-maintained role-default entry.

Control is the only profile that should select `apps.screens` and launch RFID,
LCD, USB inventory, hosted OCPP, and other non-commerce app-provided startup
behavior by default. Public commerce remains opt-in through the
`public_commerce` feature pack. Use
`ARTHEXIS_ROLE_APP_DISABLED_APPS`, `ARTHEXIS_DISABLED_APPS`, or the enabled-apps
lock command's `--disable` option only for explicit local exceptions.

## Satellite Profile

Satellite is for edge collection, charge-point monitoring,
network/device acquisition, and field nodes. It can collect data and relay
status, but should not assume Control-only local UI hardware or public commerce.

Default Satellite apps:

- `apps.discovery`
- `apps.nmcli`
- `apps.ocpp`
- `apps.protocols`
- `apps.sensors`
- `apps.serialbridge`

Optional Satellite feature packs:

- Camera collection: deprecated; no app is selected by the feature pack.
- Audio capture launch behavior: deprecated; no app is selected by the feature
  pack.
- Raspberry Pi Connect: `apps.rpiconnect`
- Screen devices: `apps.screens`, with `apps.sensors` and `apps.summary`
  selected through dependency closure.
- Local summaries: `apps.summary`

Satellite's default OCPP monitoring pack currently includes `apps.nodes`,
`apps.cards`, `apps.energy`, `apps.maps`, and `apps.protocols` through
dependency closure so charger state can be collected consistently. Satellite
should not enable repos, shop, public storefronts, FTP report handling, or
LCD/RFID launch behavior unless the node explicitly opts into those packs.
`apps.sites` remains an all-node dependency until the Pages/Sites migration
graph can be split from the Django sites migration override.

## Terminal Profile

Terminal is for single-user development, local research, documentation, and
manual tools.

Default Terminal apps:

- `apps.docs`
- `apps.imager`
- `apps.repos`
- `apps.skills`
- `apps.terminals`

Optional Terminal feature packs:

- OCPP experiments: `apps.ocpp`
- Hardware experiments: `apps.cards`, `apps.sensors`, `apps.screens`
- Screen devices: `apps.screens`, with `apps.sensors` and `apps.summary`
  selected through dependency closure.

Terminal includes `apps.imager` so a local operator with an attached USB
SD-card reader can inspect media or run `manage.py imager ...` without changing
the whole node to Control. The app still does not auto-enable the Control-only
imager burner feature on Terminal nodes.

Terminal should not enable production hosting, customer integrations, or
automatic hardware probing by default.

## Opt-In Only Apps

Most apps listed here should not be enabled on Watchtower, Satellite, or
Terminal only because the code exists, but are enabled on Control by default
through the manifest-declared app surface. Feature-pack-only apps marked in the
implementation, such as `apps.shop`, are opt-in for every role including Control
and require explicit feature pack enablement.

- `apps.clocks`
- `apps.printers`
- `apps.rpiconnect`
- `apps.screens`
- `apps.shop`
- `apps.summary`

## Conditional Import Work

Before apps can be disabled safely, these areas must stop importing optional
apps unconditionally:

- `ROUTE_PROVIDERS` should be generated from installed apps or guarded imports.
- `config/asgi.py` should include websocket routing only for installed apps.
- Middleware entries should be included only when their app is installed.
- Celery beat schedule entries should be added only when their task app is
  installed.
- Startup maintenance and health checks should skip checks for disabled apps.
- Admin modules should tolerate related optional apps being disabled.
- App manifests should declare complete `REQUIRES_APPS` dependency lists.
- `apps.celery` and `apps.celery.beat_app.CeleryBeatConfig` must remain an
  atomic baseline dependency until Celery proxy models and migrations no longer
  require `django_celery_beat`.
- `apps.nodes` hardware and network behavior must remain role guarded because
  it is selected through the OCPP dependency closure.
- `apps.sites` must stay in the all-node baseline until `pages` migrations,
  user/group `SiteTemplate` references, and the local `sites` migration override
  are split or covered by explicit reconciliation during a future baseline cut.
- `apps.ocpp` remains a required app while RFID attempts and client energy
  reports declare charger and transaction relationships. Runtime behavior still
  needs route and feature guards where profiles do not expose charger-facing
  endpoints.
- RFID login must be feature-packed: `User.login_rfid`,
  `apps.users.backends.RFIDBackend`, and its unconditional `apps.cards` imports
  must no longer require `apps.cards` when RFID is disabled.

## Migration Policy

Selective profiles should use Django's normal migration machinery for enabled
runtime apps. Disabled runtime apps should not appear in `INSTALLED_APPS` under
their normal app config, so startup preflight and suite-up checks do not import
their models, admins, routes, services, or launch behavior.

The 1.0 baseline cut removed the pre-1.0 migration-only shim packages. Fresh
installs should use the current-state migrations for preserved runtime apps
only. Pre-1.0 databases should use the explicit reinstall data import and
reconciliation path instead of relying on fresh installs to carry historical
retired-app migrations.

When an app is removed from a node profile, its existing database tables should
be left in place by default. Table cleanup is a separate destructive operation
that should require an explicit operator command.

When an app is added to a node profile, the upgrade should:

1. Recompute dependency closure.
2. Write the updated lock.
3. Restart into the new app set.
4. Run migrations for newly enabled apps.
5. Run fixture refresh for newly enabled apps.

## Acceptance Criteria

- A Watchtower node can boot without retired apps or Control-only hardware
  launch behavior in `INSTALLED_APPS`.
- OCPP runtime routes and charger-facing behavior stay explicitly guarded even
  while the app remains part of the required model baseline.
- A Watchtower node can boot without `apps.cards` after RFID login state and
  `RFIDBackend` are guarded by the RFID feature pack.
- The all-node baseline keeps `apps.celery` and
  `apps.celery.beat_app.CeleryBeatConfig` together so every selective profile
  can load Celery proxy models and migration dependencies.
- A Watchtower upgrade does not run migrations for disabled Control-only apps.
- `arthexis`, `audi`, and `porsche` can all use the Watchtower profile.
- Control nodes still enable RFID, LCD, and USB inventory by default.
- Satellite nodes enable the OCPP monitoring dependency pack without pulling in
  repos, shop, or public storefront defaults.
- Satellite nodes do not probe or launch RFID/LCD behavior unless explicitly
  opted in.
- Terminal nodes have a compact local-tooling profile.
- Route, middleware, ASGI, Celery, startup, and health-check loading are guarded
  against disabled apps.
- App manifest dependency closure prevents invalid partial profiles.
- Operators can inspect the resolved app set and understand why each app is
  enabled.

## Rollout Plan

The follow-up work is intentionally split so each step can land with focused
tests and a small blast radius:

1. [#8350](https://github.com/arthexis/arthexis/issues/8350): Add role profile
   declarations and dependency closure without changing runtime app selection.
2. [#8351](https://github.com/arthexis/arthexis/issues/8351): Build
   `INSTALLED_APPS` from the selected role profile and feature flags.
3. [#8352](https://github.com/arthexis/arthexis/issues/8352): Make route
   providers conditional on enabled apps.
4. [#8354](https://github.com/arthexis/arthexis/issues/8354): Make ASGI
   routing conditional on enabled apps.
5. [#8355](https://github.com/arthexis/arthexis/issues/8355): Make middleware
   conditional on enabled apps.
6. [#8356](https://github.com/arthexis/arthexis/issues/8356): Make Celery beat
   schedules conditional on enabled apps.
7. [#8357](https://github.com/arthexis/arthexis/issues/8357): Skip disabled
   apps in startup maintenance and health checks.
8. [#8358](https://github.com/arthexis/arthexis/issues/8358): Complete app
   manifest dependency metadata.
9. [#8359](https://github.com/arthexis/arthexis/issues/8359): Add enabled-apps
   lock tooling and setup/recovery fallback safeguards.

Each implementation step must preserve full-app setup and recovery behavior
until the node role and enabled-app lock are known. Existing tables for disabled
apps stay in place; destructive cleanup remains an explicit operator action and
is out of scope for this rollout.
