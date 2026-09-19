# Integration, GitHub-flow, and deployment readiness

The product and protocol gates are complete at `2.0.0.dev0`; this document is
the boundary before reconciliation, publication, or deployment begins. It is a
readiness checklist, not authorization to move data or alter a host.

## Product gates complete

* The frozen retained OCPP matrix has 87 implemented OCPP 1.6 and 2.0.1
  contracts, verified by `manage.py ocpp_matrix`.
* The in-process simulator exercises every retained inbound action; it is not a
  service or a deployment dependency.
* Explicit charger controls are `Charger` model APIs exposed through GWAY Django
  ingestion. `manage.py charger` is an app-wide, read-only fleet report.
* Charger enrollment and open/restricted authorization policy are complete.

## Required approval before V200-04 reconciliation

Approve all of the following in one recorded decision before any reconciliation
command, export, source inspection, or destination write is introduced:

| Retained resource | Permitted reconciliation scope | Must not enter 2.0 |
| --- | --- | --- |
| Nodes and sigils | Stable identity, topology, registration, retained roots | Host/network credentials, feature packs, runtime discovery state |
| Cards | Logical credential identity, labels, state, account/user links, authorization audit state | Raw RFID values, reader/writer state, keys, scanner services |
| Energy | Accounts, direct OCPP IDs, tariffs, balances, ledgers, charging attribution | Payment/Odoo data and unrelated reports |
| OCPP | Chargers, connectors, sessions, meter values, profiles, reservations, approved vendor/PKI metadata | Network/TLS settings, host deployment state, generic sites/media |
| Events | Only envelopes needed by an approved retained producer | Generic mail/notification history |

The source must be a read-only copy identified by revision and path. Mapping
uses domain stable identifiers rather than source primary keys. The future
reconciler must require explicit source and destination paths, generate a
redacted dry-run report, be idempotent/resumable, and keep all artifacts under
`ARTHEXIS_DATA_DIR`. It must never run from `install.sh` or mutate 1.x.

## GitHub-flow preparation

This local repository currently has no remote or upstream branch. Before any
publication, configure the intended repository and create a task branch from a
clean baseline; do not publish directly from `main`. Stage only this transition
scope, run the verification commands below, then open a review-ready PR. No
remote, branch, commit, push, issue, or pull request is created by this
readiness document.

## Deployment preparation

V200-05 remains blocked by the approved and completed V200-04 reconciliation
work. Its deployment design must stay clone-local, use a new 2.0 database, and
not create host units, system-wide links, or dependencies on the frozen 1.x
checkout. A target deployment task must supply its environment configuration
before `manage.py check --deploy` is evaluated.

## Verification record

```text
python manage.py test tests
python manage.py check --fail-level ERROR
python manage.py makemigrations --check --dry-run
python manage.py ocpp_matrix
python -m ruff check --config pyproject.toml apps arthexis tests
python -m ruff format --check --config pyproject.toml apps arthexis tests
git diff --check
```
