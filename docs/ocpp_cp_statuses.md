# Charge Point Statuses and Transitions

This document explains the charge point (CP) status values the Arthexis system expects, how each status is recorded, and how those statuses are interpreted for availability and UI presentation.

## Where CP status comes from

Charge points report their status through the OCPP `StatusNotification` message. When a CP sends a `StatusNotification`, the consumer:

- Stores the reported status in `last_status`, along with the error code, vendor info, and timestamp.
- Mirrors the update to the connector-specific `Charger` row when a connector id is provided.
- Logs the processed payload and derives the availability state (Operative/Inoperative) from the reported status.

## Expected CP status values

The system recognizes the following OCPP `StatusNotification` values. These are normalized to lowercase (for example, `Available` becomes `available`, and `SuspendedEVSE` becomes `suspendedevse`) for UI badges, aggregations, and analytics, while the PascalCase variants below reflect the raw values reported by the charge point:

- `Available`
- `Preparing`
- `Charging`
- `SuspendedEVSE`
- `SuspendedEV`
- `Finishing`
- `Faulted`
- `Unavailable`
- `Reserved`
- `Occupied`
- `OutOfService`

These values are used to build user-visible status labels and colors and to normalize status display logic.

## Operative vs. Inoperative availability

Arthexis derives a high-level availability state from OCPP status notifications:

- **Operative** statuses: `Available`, `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`, `Reserved`.
- **Inoperative** statuses: `Unavailable`, `Faulted`.

If a status notification contains one of these values, the availability state is updated to `Operative` or `Inoperative` accordingly.

## What causes each status

In Arthexis, the direct cause of a CP status is a `StatusNotification` payload from the charge point. The CP controls the value of the status field; the central system records and displays it, normalizes it for presentation, and derives availability where applicable.

Below is a summary of the expected statuses and their immediate cause in this system:

| Status value | Immediate cause in Arthexis | Availability impact |
| --- | --- | --- |
| `Available` | CP sends `StatusNotification` with `status=Available`. | Marks availability as Operative. |
| `Preparing` | CP sends `StatusNotification` with `status=Preparing`. | Marks availability as Operative. |
| `Charging` | CP sends `StatusNotification` with `status=Charging`. | Marks availability as Operative. |
| `SuspendedEV` | CP sends `StatusNotification` with `status=SuspendedEV`. | Marks availability as Operative. |
| `SuspendedEVSE` | CP sends `StatusNotification` with `status=SuspendedEVSE`. | Marks availability as Operative. |
| `Finishing` | CP sends `StatusNotification` with `status=Finishing`. | Marks availability as Operative. |
| `Reserved` | CP sends `StatusNotification` with `status=Reserved`. | Marks availability as Operative. |
| `Unavailable` | CP sends `StatusNotification` with `status=Unavailable`. | Marks availability as Inoperative. |
| `Faulted` | CP sends `StatusNotification` with `status=Faulted`. | Marks availability as Inoperative. |
| `Occupied` | CP sends `StatusNotification` with `status=Occupied` (OCPP 2.x). | No availability mapping. |
| `OutOfService` | CP sends `StatusNotification` with `status=OutOfService` (OCPP 2.x). | No availability mapping. |

## Transition diagram: StatusNotification processing

```mermaid
stateDiagram-v2
    [*] --> StatusNotification_Received
    StatusNotification_Received: CP sends StatusNotification
    StatusNotification_Received --> Persist_Status
    Persist_Status: Update last_status/last_error_code/vendor info/timestamp
    Persist_Status --> Update_Connector
    Update_Connector: Mirror status to connector-specific Charger row
    Update_Connector --> Derive_Availability
    Derive_Availability: Operative/Inoperative if status matches
    Derive_Availability --> Log_Event
    Log_Event: Store "StatusNotification processed" log entry
    Log_Event --> [*]
```

## Transition diagram: Availability state derivation

```mermaid
flowchart TD
    Status[StatusNotification.status]
    Status -->|Available/Preparing/Charging/SuspendedEV/SuspendedEVSE/Finishing/Reserved| Operative[availability_state = Operative]
    Status -->|Unavailable/Faulted| Inoperative[availability_state = Inoperative]
    Status -->|Anything else| NoChange[availability_state unchanged]
```

## Transition diagram: UI override rules for display

The UI sometimes overrides the raw status so the badge reflects active transactions. The decision logic is summarized below.

```mermaid
flowchart TD
    Input[Status + Active Session + Error Code]
    Input -->|Session active & no error & status empty/unknown/Available| DisplayCharging[Display Charging badge]
    Input -->|No session & no error & status Charging/Finishing & derived state not Available| DisplayChargingFallback[Display Charging badge]
    Input -->|Error code present| DisplayFault[Display status + error badge]
    Input -->|Otherwise| DisplayRaw[Display normalized status badge]
```

## Notes for operators

- The system does **not** generate status values on its own; it only records what the CP reports.
- If a CP reports non-standard status values, Arthexis will still store them and display the raw value, but availability state changes only occur for the recognized Operative/Inoperative statuses.
