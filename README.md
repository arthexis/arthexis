# Constellation

## Purpose

Arthexis Constellation is a Django-based software suite for managing electric vehicle charging infrastructure, charger connectivity, operational state, transactions, authorization, and retained OCPP workflows.

The current 2.0 runtime focuses on a small, explicit application surface: charger and station records, OCPP transport and protocol behavior, operational state, administration, reconciliation, and operator-facing guidance.

## Suite Features

- Supports the retained **OCPP 1.6** and **OCPP 2.0.1** protocol surfaces.
- Negotiates charger WebSocket connections by protocol version and keeps live connection state separate from persisted charger identity.
- Records charger status, transactions, meter values, reservations, charging profiles, configuration or variables, notifications, certificates, and operation outcomes where required by the retained protocol contract.
- Supports charger enrollment with an explicit enrollment credential and per-charger open or restricted authorization policy.
- Provides typed reset, remote-start, and remote-stop operations for a selected charger.
- Provides a read-only charger fleet report with connection, session, connector, and energy state.
- Keeps outbound charger operations explicit: retained protocol operations are available without automatically scheduling firmware, diagnostics, reservations, configuration rollout, or maintenance work.

Every retained action below is covered by the executable support matrix and has a concrete inbound handler or outbound validator/emitter.

### Charge point / charging station → CSMS

| Action | 1.6 | 2.0.1 | What we do |
| --- | --- | --- | --- |
| `Authorize` | ✅ | ✅ | Validate an identification token according to the charger's authorization policy and retain the decision. |
| `BootNotification` | ✅ | ✅ | Register charger identity and boot information and update its operational record. |
| `ClearedChargingLimit` | — | ✅ | Accept reports that a previously imposed charging limit has been cleared. |
| `CostUpdated` | — | ✅ | Accept transaction cost updates reported by the charging station. |
| `DataTransfer` | ✅ | ✅ | Accept vendor-specific protocol payloads through the retained data-transfer boundary. |
| `DiagnosticsStatusNotification` | ✅ | — | Track OCPP 1.6 diagnostics-upload progress. |
| `FirmwareStatusNotification` | ✅ | ✅ | Track charger firmware-update lifecycle state. |
| `Get15118EVCertificate` | — | ✅ | Process ISO 15118 EV-certificate requests through the certificate domain boundary. |
| `GetCertificateStatus` | — | ✅ | Process charging-station certificate-status requests. |
| `Heartbeat` | ✅ | ✅ | Refresh liveness and last-contact state for the connected charger. |
| `LogStatusNotification` | — | ✅ | Track log-upload progress for OCPP 2.0.1 diagnostics. |
| `MeterValues` | ✅ | ✅ | Persist retained metering values, units, multipliers, and transaction energy data. |
| `NotifyChargingLimit` | — | ✅ | Record charging-limit notifications reported by the station. |
| `NotifyCustomerInformation` | — | ✅ | Accept customer-information report fragments. |
| `NotifyDisplayMessages` | — | ✅ | Accept display-message reports from the charging station. |
| `NotifyEVChargingNeeds` | — | ✅ | Accept EV charging-needs information for the active session. |
| `NotifyEVChargingSchedule` | — | ✅ | Accept EV charging-schedule information reported by the station. |
| `NotifyEvent` | — | ✅ | Persist retained monitoring and operational event notifications. |
| `NotifyMonitoringReport` | — | ✅ | Accept monitoring-report fragments. |
| `NotifyReport` | — | ✅ | Accept general OCPP 2.0.1 report fragments. |
| `PublishFirmwareStatusNotification` | — | ✅ | Track publish-firmware lifecycle state. |
| `ReportChargingProfiles` | — | ✅ | Accept charging-profile reports from the station. |
| `ReservationStatusUpdate` | — | ✅ | Track reservation status changes reported by the station. |
| `SecurityEventNotification` | — | ✅ | Record security events without persisting private certificate material. |
| `SignCertificate` | — | ✅ | Accept certificate-signing requests through the retained certificate workflow. |
| `StartTransaction` | ✅ | — | Open an OCPP 1.6 transaction and associate its initial authorization and meter state. |
| `StatusNotification` | ✅ | ✅ | Update connector or EVSE availability and fault state. |
| `StopTransaction` | ✅ | — | Close an OCPP 1.6 transaction and retain its final meter and stop state. |
| `TransactionEvent` | — | ✅ | Create, update, or close OCPP 2.0.1 transaction state from transaction events. |

### CSMS → charge point / charging station

| Action | 1.6 | 2.0.1 | What we do |
| --- | --- | --- | --- |
| `CancelReservation` | ✅ | ✅ | Compose and correlate a request to cancel a reservation. |
| `CertificateSigned` | — | ✅ | Deliver a signed certificate response to a charging station. |
| `ChangeAvailability` | ✅ | ✅ | Request an operative or inoperative availability state. |
| `ChangeConfiguration` | ✅ | — | Set an OCPP 1.6 configuration key through the explicit operation boundary. |
| `ClearChargingProfile` | ✅ | ✅ | Request removal of matching charging profiles. |
| `ClearDisplayMessage` | — | ✅ | Request removal of a charging-station display message. |
| `ClearVariableMonitoring` | — | ✅ | Clear selected OCPP 2.0.1 variable-monitoring settings. |
| `CustomerInformation` | — | ✅ | Request customer-information reporting or deletion. |
| `DataTransfer` | ✅ | ✅ | Send an explicit vendor-specific data-transfer request. |
| `DeleteCertificate` | — | ✅ | Request deletion of a selected installed certificate. |
| `GetBaseReport` | — | ✅ | Request one of the standard OCPP 2.0.1 base reports. |
| `GetCompositeSchedule` | ✅ | ✅ | Request a calculated charging schedule from the charger. |
| `GetConfiguration` | ✅ | — | Read selected OCPP 1.6 configuration keys. |
| `GetDiagnostics` | ✅ | — | Request an OCPP 1.6 diagnostics upload. |
| `GetDisplayMessages` | — | ✅ | Request display messages currently known by the charging station. |
| `GetInstalledCertificateIds` | — | ✅ | Request metadata identifying installed certificates. |
| `GetLocalListVersion` | ✅ | ✅ | Read the charger's local authorization-list version. |
| `GetLog` | — | ✅ | Request an OCPP 2.0.1 diagnostics or security log upload. |
| `GetReport` | — | ✅ | Request a filtered OCPP 2.0.1 component/variable report. |
| `GetVariables` | — | ✅ | Read selected OCPP 2.0.1 variables. |
| `InstallCertificate` | — | ✅ | Request installation of a certificate on the charging station. |
| `PublishFirmware` | — | ✅ | Request that the charging station publish firmware information. |
| `RemoteStartTransaction` | ✅ | — | Start an OCPP 1.6 transaction remotely for an identification token. |
| `RemoteStopTransaction` | ✅ | — | Stop a selected OCPP 1.6 transaction remotely. |
| `RequestStartTransaction` | — | ✅ | Start an OCPP 2.0.1 transaction remotely for an identification token. |
| `RequestStopTransaction` | — | ✅ | Stop a selected OCPP 2.0.1 transaction remotely. |
| `ReserveNow` | ✅ | ✅ | Request a connector or EVSE reservation. |
| `Reset` | ✅ | ✅ | Request a graceful or immediate charger reset. |
| `SendLocalList` | ✅ | ✅ | Publish an updated local authorization list. |
| `SetChargingProfile` | ✅ | ✅ | Apply a charging profile to the charger. |
| `SetDisplayMessage` | — | ✅ | Configure a display message on the charging station. |
| `SetMonitoringBase` | — | ✅ | Select the charging station's monitoring-base behavior. |
| `SetMonitoringLevel` | — | ✅ | Select the station-wide monitoring severity threshold. |
| `SetVariableMonitoring` | — | ✅ | Configure monitoring criteria for selected variables. |
| `SetVariables` | — | ✅ | Set selected OCPP 2.0.1 variables. |
| `TriggerMessage` | ✅ | ✅ | Ask the charger to emit a supported message immediately. |
| `UnlockConnector` | ✅ | ✅ | Request release of a locked connector. |
| `UpdateFirmware` | ✅ | ✅ | Request a charger firmware update and correlate its outcome. |

## Role Architecture

Arthexis retains four node-role identities for topology and deployment modeling. In 2.0 they are configuration identities, not automatic bundles of legacy host features.

| Role | Intended place in a constellation |
| --- | --- |
| **Terminal** | A local or single-user Arthexis node. |
| **Control** | A node associated with local equipment or a focused operational task. |
| **Satellite** | An edge or remote node participating in a wider constellation. |
| **Watchtower** | A central or hosted node used for shared access and orchestration. |

Role-specific capabilities can be built on top of these identities without requiring the application model to dynamically install or remove Django apps.

## Quick Guide

### 1. Clone

```bash
git clone https://github.com/arthexis/arthexis.git
cd arthexis
```

### 2. Install

For a fresh 2.0 data directory:

```bash
./install.sh
```

The installer creates the local virtual environment, installs the pinned application requirements, validates the destination database generation, applies migrations, and seeds required application state.

A legacy database is never upgraded in place. When an explicit import is required, pass it as a separate read-only source:

```bash
./install.sh --import /path/to/legacy.sqlite3
```

### 3. Run locally

After installation, run the ASGI application with the project environment:

```bash
.venv/bin/python -m arthexis.server
```

The default application endpoint is `127.0.0.1:8888`.

### 4. Inspect the charger fleet

The fleet command is read-only:

```bash
.venv/bin/python manage.py fleet
```

It can filter by charger identity and by enabled, connected, or charging state, and can include connector, transaction, and energy detail.

### 5. Administration

Django administration remains available at `/admin/` for authorized staff. It is intentionally not linked from the public site navigation.

The administration surface exposes retained application records and operation outcomes; it is not a raw OCPP payload console.

### 6. Operator guidance

Start with the [Operator Guide](docs/operator-guide.md) for deployment-health and operating notes that are appropriate to expose through the Markdown site.

## Development

The 2.0 repository is a clean reimplementation. Current source, migrations, tests, and executable protocol matrices are authoritative; the frozen 1.x branch is retained only as a historical and reconciliation source.

Useful validation commands include:

```bash
.venv/bin/python manage.py check --fail-level ERROR
.venv/bin/python manage.py ocpp_matrix
.venv/bin/python manage.py test tests
```

The `ocpp_matrix` command reports implementation status for every retained OCPP 1.6 and OCPP 2.0.1 action contract and fails if any retained action lacks an executable implementation.

## Support

Arthexis Constellation is actively maintained with operational guidance and protocol coverage kept alongside the application source.

For professional services and commercial support, contact [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) or visit [Gelectriic](https://www.gelectriic.com/).
