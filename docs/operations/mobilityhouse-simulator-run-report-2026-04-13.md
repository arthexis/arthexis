# Mobility House simulator run report (2026-04-13)

## Scope

Run the Mobility House simulator against the Arthexis charge point WebSocket endpoint, fix issues encountered during execution, and capture the reproducible process/results.

## Environment

- Repo: `arthexis`
- Date: 2026-04-13 (UTC)
- Python env: `.venv`
- Web server target: `ws://127.0.0.1:8000/ocpp/c/CP2`
- Simulator slot: `1`
- Backend: `mobilityhouse`

## Process

1. Refreshed dependencies with `./env-refresh.sh --deps-only`.
2. Attempted to run simulator command with Mobility House backend.
3. Hit backend selection failure:
   - `CommandError: Unsupported backend 'mobilityhouse'. Enabled backends: arthexis`
4. Investigated simulator backend selection and found Mobility House fallback defaulted to disabled in runtime selection logic when feature metadata is unset.
5. Fixed runtime defaults so Mobility House matches feature parameter defaults (`enabled`) when explicit metadata is absent.
6. Added regression coverage to protect the default behavior.
7. Installed optional `ocpp` dependency required by Mobility House runtime: `.venv/bin/pip install ocpp==0.26.0`.
8. Ran DB migrations (`.venv/bin/python manage.py migrate`) so suite feature/runtime tables exist.
9. Started Django ASGI dev server and ran simulator against charge point socket target.
10. Captured simulator status/log evidence.

## Code changes made

- `apps/simulators/simulator_runtime.py`
  - Changed `mobilityhouse_backend` fallback default from disabled to enabled in:
    - `get_simulator_backend_choices`
    - `resolve_simulator_backend`
- `apps/ocpp/tests/test_simulator_runtime_backend_selection.py`
  - Added `test_backend_selection_defaults_enable_mobilityhouse_parameter`.

## Commands used

```bash
./env-refresh.sh --deps-only
.venv/bin/pip install ocpp==0.26.0
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver 127.0.0.1:8000
.venv/bin/python manage.py feature ocpp-simulator --disabled
.venv/bin/python manage.py simulator stop --slot 1
.venv/bin/python manage.py simulator start --slot 1 --host 127.0.0.1 --ws-port 8000 --cp-path ocpp/c/CP2 --serial-number CP2 --backend mobilityhouse --duration 15 --interval 2 --meter-interval 2 --allow-private-network
.venv/bin/python manage.py simulator status --slot 1
```

## Result

- Simulator startup command succeeded with `Connection accepted`.
- Status confirms running simulator with backend `mobilityhouse` and WebSocket target settings.
- Log file written at `logs/simulator.ocpp_c_CP2.log` with successful connection and BootNotification transmission.

Example observed log lines:

- `Connected (subprotocol=ocpp1.6j)`
- `> [2, "boot-1", "BootNotification", ...]`

## Notes

- When the suite feature `ocpp-simulator` is enabled, starts are queued to `cpsim-service` rather than run inline. For direct CLI runtime in this environment, disabling that feature was used during verification.
- After verification, re-enable service mode if desired:

```bash
.venv/bin/python manage.py feature ocpp-simulator --enabled
```
