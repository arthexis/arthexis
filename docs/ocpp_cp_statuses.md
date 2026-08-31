# CP Statuses and Transitions

This page summarizes the charge point status values Arthexis expects and how they map to operator-visible availability.

For full handling details (including badge rendering behavior when active sessions lag raw status updates), see the OCPP manual section on [`StatusNotification`](development/ocpp-user-manual.md#statusnotification).

## Canonical charge point statuses

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

Arthexis normalizes these values internally to lowercase for display and aggregation consistency.

## Availability mapping

- **Operative**: `Available`, `Preparing`, `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing`, `Reserved`
- **Inoperative**: `Unavailable`, `Faulted`
- **No availability state change**: `Occupied`, `OutOfService`, or any other non-mapped status

The charge point remains the source of truth for status text; Arthexis records what the device reports and applies only the explicit availability mapping above.
