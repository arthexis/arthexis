# Bluetooth Assistant Sidecar Plan

## Context

Arthexis should be able to respond to local Bluetooth headset button gestures, including assistant/call-button gestures from hardware such as the Plantronics Voyager 5200. The implementation should extend the suite through a first-class Django app and a node-scoped sidecar service rather than a disconnected helper.

The closest existing pattern is the RFID scanner service: local hardware access runs in a sidecar, emits lock/log artifacts, exposes basic service status, and lets Django ingest auditable events. Bluetooth support should follow that pattern while accounting for headset profile differences across BlueZ, AVRCP, HFP, and Linux input event paths.

## Proposed Shape

Create a web-capable `bluetooth` app with:

- Django models for adapters, trusted devices, button events, command profiles, and assistant sessions.
- Admin configuration for device trust, event learning, command mappings, and event audit.
- A sidecar launched as `python -m apps.bluetooth.button_service`.
- Lifecycle service wiring for `bluetooth-{service}.service`.
- A local authenticated ingest endpoint plus lock/log fallback artifacts.
- A learn mode because real headset event names differ by firmware, pairing profile, and host stack.

The sidecar should not write directly to the database. It should emit `logs/bluetooth-events.ndjson` and `.locks/bluetooth-event.json`, and may additionally POST to a local authenticated ingest endpoint for lower latency.

## Command Dispatch Policy

Button mappings should dispatch only explicit Arthexis capabilities:

- Registered internal actions from `apps.actions`.
- Approved Django management command targets with structured arguments.
- Optional sigil/script targets when the administrator deliberately enables them.

Do not execute arbitrary shell strings. Preserve administrator power through explicit configuration and auditing rather than broad runtime command execution.

## Proposed Models

- `BluetoothAdapter`: local adapter identifier, address, powered/discoverable state, and last probe metadata.
- `BluetoothDevice`: trusted device address, alias, class/profile metadata, pairing state, and last seen timestamp.
- `BluetoothButtonEvent`: raw event payload, normalized event key, source path, debounce key, status, and handling result.
- `BluetoothCommandProfile`: device/event mapping to an approved suite action or command.
- `BluetoothAssistantSession`: optional push-to-talk session metadata, a link to file-backed audio records such as `AudioSample` or `ContentSample`, transcript, parsed command, and result. Do not store audio blobs directly in the Bluetooth app tables.

## Plan Tasks

### Task BT-1: Scaffold Bluetooth App

* Intent: Create the `bluetooth` Django app and baseline domain models for adapters, devices, button events, command profiles, and assistant sessions.
* Scope: `apps/bluetooth/`, `apps/bluetooth/models.py`, `apps/bluetooth/admin.py`, `apps/bluetooth/manifest.py`, `apps/bluetooth/migrations/`, `apps/bluetooth/tests/`.
* Constraints: Use the repository-specific `.venv/bin/python manage.py create app bluetooth` scaffold command; keep the app web-capable because it needs admin and ingest routes; create admin configuration using suite patterns; keep model fields auditable and avoid storing secret button-ingest credentials in plaintext.
* Acceptance Criteria: The app loads through its manifest, models are registered in admin, migrations exist, and smoke tests prove the app can be imported and the core models can be created.
* Verification Commands: `.venv/bin/python manage.py makemigrations bluetooth --check --dry-run`; `.venv/bin/python manage.py migrations check`; `.venv/bin/python manage.py test run -- apps.bluetooth`.
* Out of Scope: BlueZ integration, systemd unit installation, speech transcription, and command dispatch execution.
* Depends on: none
* Blocks: BT-2, BT-3, BT-4
* Parallel-safe: no
* Risk/Rollback: Risk level low. Primary failure mode is an app scaffold or migration shape that drifts from repository policy. Roll back by removing `apps/bluetooth/` and its migration references before release.

### Task BT-2: Implement Event Ingest and Audit

* Intent: Provide the Django-side ingestion path for sidecar events and persist auditable button-event history.
* Scope: `apps/bluetooth/api/`, `apps/bluetooth/ingest.py`, `apps/bluetooth/models.py`, `apps/bluetooth/admin.py`, `apps/bluetooth/tests/`.
* Constraints: Authenticate local sidecar ingest with a scoped token or equivalent existing service-token pattern; support NDJSON lock/log ingestion as a fallback; dedupe repeated button events; reject events from untrusted devices unless learn mode is active.
* Acceptance Criteria: A valid ingest request creates a `BluetoothButtonEvent`; duplicate events inside the debounce window do not execute twice; untrusted events are recorded as rejected unless learn mode is active; admin audit shows handled and rejected outcomes.
* Verification Commands: `.venv/bin/python manage.py makemigrations bluetooth --check --dry-run`; `.venv/bin/python manage.py migrations check`; `.venv/bin/python manage.py test run -- apps.bluetooth`.
* Out of Scope: Host Bluetooth event collection and systemd service installation.
* Depends on: BT-1
* Blocks: BT-3, BT-4
* Parallel-safe: no
* Risk/Rollback: Risk level medium. Primary failure mode is accepting spoofed local events. Roll back by disabling the ingest URL and command profiles while preserving event audit rows for investigation.

### Task BT-3: Build Button Sidecar Service

* Intent: Add the local process that listens for Bluetooth headset button events and emits Arthexis event artifacts.
* Scope: `apps/bluetooth/button_service.py`, `apps/bluetooth/service_client.py`, `apps/bluetooth/management/commands/bluetooth.py`, `apps/bluetooth/tests/`, `requirements-hw.txt`, `pyproject.toml` optional hardware extras if a new dependency is required.
* Constraints: Launch as `python -m apps.bluetooth.button_service`; use BlueZ and Linux input integrations defensively because devices expose assistant buttons differently; provide `ping`, `status`, and `learn` behavior; keep the sidecar free of direct database writes; prefer standard-library or system tools before adding dependencies.
* Acceptance Criteria: The module entrypoint initializes the suite environment, exposes status, records learned raw events, writes `logs/bluetooth-events.ndjson`, updates `.locks/bluetooth-event.json`, and can POST to the ingest endpoint when configured.
* Verification Commands: `.venv/bin/python manage.py test run -- apps.bluetooth`; `.venv/bin/python manage.py bluetooth status`; `.venv/bin/python -m apps.bluetooth.button_service --help`.
* Out of Scope: Production systemd installation and full voice transcription.
* Depends on: BT-1, BT-2
* Blocks: BT-4, BT-5
* Parallel-safe: no
* Risk/Rollback: Risk level medium. Primary failure mode is host-specific Bluetooth event handling not matching a headset profile. Roll back by stopping the sidecar and leaving learned raw events for adapter-specific fixes.

### Task BT-4: Wire Lifecycle Service and Node Feature

* Intent: Make the Bluetooth assistant sidecar manageable through existing Arthexis lifecycle and node-feature surfaces.
* Scope: `apps/nodes/fixtures/node_features__nodefeature_bluetooth_assistant.json`, `apps/services/fixtures/lifecycle_services__lifecycleservice_bluetooth_assistant.json`, `apps/nodes/feature_registry.py`, `apps/bluetooth/node_features.py`, `apps/nodes/models/features.py`, `apps/services/`, `scripts/helpers/systemd_locks.sh`, `configure.sh`, `install.sh`, `scripts/rename_service`, `docs/suite-services-report.md`, `docs/services/bluetooth-assistant-service.md`, tests under `apps/services/tests/`, `apps/nodes/tests/`, and `apps/bluetooth/tests/`.
* Constraints: Use a lock file such as `.locks/bluetooth-service.lck`; register `bluetooth-{service}.service`; list the service in Suite Services Report even when not configured; keep install/configure toggles symmetrical with RFID and camera service toggles.
* Acceptance Criteria: Operators can enable and disable the sidecar through install/configure flows; lifecycle reconciliation writes the correct lock and unit records; node feature defaults include the Bluetooth assistant slug where appropriate; service rename preserves Bluetooth companion units; docs explain enablement, learn mode, troubleshooting, and rollback.
* Verification Commands: `.venv/bin/python manage.py migrations check`; `.venv/bin/python manage.py test run -- apps.bluetooth apps.nodes apps.services`; shellcheck-equivalent existing script validation if available.
* Out of Scope: Implementing command dispatch internals beyond service availability.
* Depends on: BT-2, BT-3
* Blocks: BT-5
* Parallel-safe: no
* Risk/Rollback: Risk level medium. Primary failure mode is lifecycle script drift from existing companion service behavior. Roll back by removing the Bluetooth lock, service unit, lifecycle seed, and node feature fixture.

### Task BT-5: Add Command Dispatch Profiles

* Intent: Let administrators map trusted button events to approved Arthexis actions and commands.
* Scope: `apps/bluetooth/dispatcher.py`, `apps/bluetooth/models.py`, `apps/bluetooth/admin.py`, `apps/actions/`, `apps/sigils/` integration points if selected, and `apps/bluetooth/tests/`.
* Constraints: Preserve administrator flexibility without arbitrary shell execution; store structured command targets; require explicit device trust; log every dispatch attempt and result; include a dry-run/test action in admin.
* Acceptance Criteria: A trusted device event can invoke a configured internal action or approved management command; dispatch records success and failure details; rejected mappings are visible in admin; tests cover success, permission rejection, dedupe, and command error behavior.
* Verification Commands: `.venv/bin/python manage.py makemigrations bluetooth --check --dry-run`; `.venv/bin/python manage.py migrations check`; `.venv/bin/python manage.py test run -- apps.bluetooth apps.actions`.
* Out of Scope: Speech-to-text command parsing and remote cloud assistant integrations.
* Depends on: BT-2, BT-3, BT-4
* Blocks: none
* Parallel-safe: no
* Risk/Rollback: Risk level medium. Primary failure mode is overly broad command execution. Roll back by disabling command profiles and keeping only event ingestion/audit active.

## Implementation Notes

- Use the local non-container Arthexis instance for development and validation.
- Run `./env-refresh.sh --deps-only` before tests if the local environment is not already bootstrapped.
- Keep README files untouched unless explicitly requested and validated.
- Treat device-specific behavior as data learned from the local host, not as hardcoded Plantronics-only behavior.
- Add adjacent health improvements where the touched areas reveal obvious test, docs, or security gaps.
