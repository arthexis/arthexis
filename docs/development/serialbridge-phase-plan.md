# Serial bridge implementation plan

## Phase 1 status

Phase 1 is implemented in `apps.serialbridge` with:

- admin-managed serial interface records for UART/RS485 transport settings
- peer identity and key fingerprint tracking
- session status and tx/rx counters
- command audit records for ping and recovery commands
- `serialbridge ping` subcommand for first-hop health validation
- `serialbridge recover` subcommand with bounded `tail_logs`, `diagnostics_manifest`, allowlisted `restart_service`, `safe_mode`, and `--restore-network <eth0|wlan0|wlan1>`

## Out-of-the-box install behavior

New installs now seed a default enabled interface:

- `Primary UART` on `/dev/ttyS0`
- role `target`, mode `UART`, `115200/N/1`

This keeps direct Pi-to-Pi recovery lane setup available immediately after imager provisioning.

## Phase 3: RS485 multi-node

Planned multi-node features:

- bus addressing and node registry policies
- retry policy/timing profile per interface
- collision/backoff telemetry on session model

## Phase 4: interoperability

Planned interoperability options:

- optional Modbus RTU register map for health/status
- mapping layer documentation for integrators
- per-interface protocol mode flags
