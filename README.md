# Constellation

[![OCA · OCPP 1.6](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/ocpp-spec-refresh.yml?branch=main&style=flat-square&label=OCA%20%C2%B7%20OCPP%201.6)](https://github.com/arthexis/arthexis/actions/workflows/ocpp-spec-refresh.yml)
[![OCA · OCPP 2.0.1](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/ocpp-spec-refresh.yml?branch=main&style=flat-square&label=OCA%20%C2%B7%20OCPP%202.0.1)](https://github.com/arthexis/arthexis/actions/workflows/ocpp-spec-refresh.yml)

## Purpose

Arthexis Constellation is a Django-based software suite for managing electric vehicle charging infrastructure, charger connectivity, operational state, transactions, authorization, and retained OCPP workflows.


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

## Operational Capabilities

The protocol matrix describes what Arthexis can exchange with a charger. The application also provides the following operational surfaces around those protocol messages.

| Capability | Behavior |
| --- | --- |
| **Fleet inspection** | Read configured chargers as a captured fleet snapshot, optionally filtering by charger identity, enabled state, connection state, or charging state. Detail mode includes connector, transaction timing, and resolvable energy information. |
| **Charger enrollment** | Enroll an unknown charger only after successful OCPP subprotocol negotiation and presentation of the configured enrollment credential. Invalid or missing enrollment credentials do not create charger records. |
| **Authorization policy** | Configure each charger for `open` or `restricted` authorization. Open mode accepts a non-empty protocol-valid identifier while retaining an audit record; restricted mode requires an active matching card or account. |
| **Explicit charger control** | Request reset, remote start, or remote stop for one selected charger. Arthexis derives the correct OCPP 1.6 or 2.0.1 action from the charger's configured protocol and rejects incompatible options before delivery. |
| **Operation correlation** | Track outbound protocol calls through pending, completed, errored, timed-out, and disconnected outcomes without treating an attempted send as a successful charger action. |
| **Administration** | Inspect and maintain retained application records through Django admin, including chargers, sessions, operation outcomes, authorization state, certificates, reservations, profiles, and related records. |
| **Structured events** | Publish and persist typed event envelopes with an event type, producer, JSON payload, creation time, and publication time. |
| **Energy and account records** | Maintain customer accounts, tariffs, kWh balances, ledger entries, logical card credentials, and charging attribution data used by retained authorization and accounting flows. |
| **Node topology** | Record Terminal, Control, Satellite, and Watchtower node identities and explicit links between nodes without dynamically changing the installed Django application set. |

### Explicit control boundary

Arthexis deliberately separates **protocol support** from **automatic orchestration**. A retained outbound OCPP action can be validated, emitted, and correlated when an administrator or integration explicitly requests it, but the application does not automatically schedule charger firmware updates, diagnostics, configuration rollouts, reservations, resets, starts, or stops.

The initial high-level charger controls are intentionally narrow:

| Operation | OCPP 1.6 | OCPP 2.0.1 | Behavior |
| --- | --- | --- | --- |
| **Reset** | `Reset` | `Reset` | Graceful by default; an explicit hard option requests the immediate reset form for the selected protocol. |
| **Start** | `RemoteStartTransaction` | `RequestStartTransaction` | Requires an identification token and accepts only the connector/EVSE selector appropriate to the configured protocol. |
| **Stop** | `RemoteStopTransaction` | `RequestStopTransaction` | Uses the sole active local transaction when unambiguous, or requires an explicit transaction selector when several are active. |

## Role Architecture

Arthexis retains four node-role identities for topology and deployment modeling. They are configuration identities rather than automatic bundles of host features.

| Role | Intended place in a constellation |
| --- | --- |
| **Terminal** | A local or single-user Arthexis node. |
| **Control** | A node associated with local equipment or a focused operational task. |
| **Satellite** | An edge or remote node participating in a wider constellation. |
| **Watchtower** | A central or hosted node used for shared access and orchestration. |

Role-specific capabilities can be built on top of these identities without requiring the application model to dynamically install or remove Django apps.

## Quick Guide

### Installation

Arthexis can be installed directly or through [Gway](https://github.com/arthexis/gway).

#### Option 1: Manual installation

Clone the repository and run the bundled installer:

```bash
git clone https://github.com/arthexis/arthexis.git
cd arthexis
./install.sh
```

The installer creates the local virtual environment, installs the pinned application requirements, prepares the database, applies migrations, and seeds required application state.

#### Option 2: Install with Gway

Install [Gway](https://github.com/arthexis/gway), then let it install and prepare Arthexis:

```bash
python -m pip install gway
gway install arthexis/arthexis
gway arthexis migrate --noinput
gway arthexis seed
```

Gway manages the project installation while Arthexis retains its own application and protocol behavior.

### Run locally


After installation, run the ASGI application with the project environment:

```bash
.venv/bin/daphne -b 127.0.0.1 -p 8888 arthexis.asgi:application
```

The default application endpoint is `127.0.0.1:8888`.

### Inspect the charger fleet

The fleet command is read-only:

```bash
.venv/bin/python manage.py fleet
```

It can filter by charger identity and by enabled, connected, or charging state, and can include connector, transaction, and energy detail.

### Publish a structured event

Events can be published explicitly from the application command line:

```bash
.venv/bin/python manage.py event publish charger.audit --producer operator --payload '{"source":"manual"}'
```

The payload must be a JSON object. The command persists the event envelope and prints its generated event identifier.

### Administration

Django administration remains available at `/admin/` for authorized staff. It is intentionally not linked from the public site navigation.

The administration surface exposes retained application records and operation outcomes; it is not a raw OCPP payload console.

### Operator guidance

Start with the [Operator Guide](docs/operator-guide.md) for deployment-health and operating notes that are appropriate to expose through the Markdown site.

## Support

Arthexis Constellation is actively maintained with operational guidance and protocol coverage kept alongside the application source.

For professional services and commercial support, contact [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) or visit [Gelectriic](https://www.gelectriic.com/).
