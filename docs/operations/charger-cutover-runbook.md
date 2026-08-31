# Charger Blue/Green Cutover Runbook

> **Deprecated:** Charger blue/green cutover support is deprecated and
> unsupported. It never worked reliably enough to remain an OCPP admission
> control, and the `charger_cutovers`/`charger-cutovers` feature pack is rejected
> during app profile resolution instead of silently enabling charger-facing OCPP
> without the retired policy machinery.

Do not use this runbook for new operations. Remove `charger_cutovers` from
`ARTHEXIS_ROLE_APP_FEATURE_PACKS` or `ARTHEXIS_FEATURE_PACKS` before starting a
node. Use the standard hosted OCPP deployment and perform charger migration with
operator-managed ingress/DNS/network controls outside of the deprecated suite
cutover feature.

## Deprecated commands

The retired blue/green workflow previously documented these commands:

- `python manage.py cutover status --plan <plan_id>`
- `python manage.py cutover ready --plan <plan_id>`
- `python manage.py cutover start --plan <plan_id>`
- `python manage.py cutover reset-switchover --plan <plan_id> --charger <charger_id> [--hard]`
- `python manage.py cutover drain --plan <plan_id> [--force-reconnect]`
- `python manage.py cutover rollback --plan <plan_id> --reason "..."`

They are kept here only as historical context for operators cleaning up old
configuration references. Do not rely on them for charger isolation.
