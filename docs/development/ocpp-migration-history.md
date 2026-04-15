# OCPP migration history

This document keeps historical OCPP migration notes for teams upgrading older Arthexis releases.

## Deprecated command removal

Legacy single-purpose commands (`coverage_ocpp16`, `coverage_ocpp201`, `coverage_ocpp21`, `import_transactions`, `export_transactions`, and `ocpp_replay`) were removed in favor of the unified `ocpp` command surface.

If automation still calls legacy entrypoints, update scripts to the canonical `ocpp` subcommands:

| Removed command | Replacement |
| --- | --- |
| `.venv/bin/python manage.py coverage_ocpp16` | `.venv/bin/python manage.py ocpp coverage --version 1.6` |
| `.venv/bin/python manage.py coverage_ocpp201` | `.venv/bin/python manage.py ocpp coverage --version 2.0.1` |
| `.venv/bin/python manage.py coverage_ocpp21` | `.venv/bin/python manage.py ocpp coverage --version 2.1` |
| `.venv/bin/python manage.py import_transactions <input.json>` | `.venv/bin/python manage.py ocpp transactions import <input.json>` |
| `.venv/bin/python manage.py export_transactions <output.json> [--start ... --end ... --chargers ...]` | `.venv/bin/python manage.py ocpp transactions export <output.json> [--start ... --end ... --chargers ...]` |
| `.venv/bin/python manage.py ocpp_replay <extract.json>` | `.venv/bin/python manage.py ocpp trace replay <extract.json>` |

## Forwarder import path removal

The compatibility shim at `apps.ocpp.forwarder` was removed.

If integration code still imports the legacy path, switch to `apps.forwarder.ocpp`:

```python
# from apps.ocpp.forwarder import Forwarder, ForwardingSession, forwarder
from apps.forwarder.ocpp import Forwarder, ForwardingSession, forwarder
```

## Simulator import path removal

The compatibility package at `apps.ocpp.simulator` was removed.

If external integrations still import the legacy module path, update imports to `apps.simulators`:

| Removed import path | Replacement import path |
| --- | --- |
| `from apps.ocpp.simulator import ChargePointSimulator, SimulatorConfig` | `from apps.simulators import ChargePointSimulator, SimulatorConfig` |

## EVCS wrapper removal

The compatibility wrapper module at `apps.ocpp.evcs` was removed.

External plugin maintainers should update imports to use `apps.simulators.evcs` directly:

| Removed import path | Replacement import path |
| --- | --- |
| `from apps.ocpp.evcs import simulate, simulate_cp, view_simulator, view_cp_simulator` | `from apps.simulators.evcs import simulate, simulate_cp, view_simulator, view_cp_simulator` |
| `from apps.ocpp.evcs import _simulator_status_json, _start_simulator, _stop_simulator, get_simulator_state, parse_repeat` | `from apps.simulators.evcs import _simulator_status_json, _start_simulator, _stop_simulator, get_simulator_state, parse_repeat` |

## One-time shell migration example

For shell migration, a direct one-time update can be done with substitutions like:

```bash
# GNU sed
sed -i \
  -e 's|.venv/bin/python manage.py coverage_ocpp16|.venv/bin/python manage.py ocpp coverage --version 1.6|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp201|.venv/bin/python manage.py ocpp coverage --version 2.0.1|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp21|.venv/bin/python manage.py ocpp coverage --version 2.1|g' \
  -e 's|.venv/bin/python manage.py import_transactions|.venv/bin/python manage.py ocpp transactions import|g' \
  -e 's|.venv/bin/python manage.py export_transactions|.venv/bin/python manage.py ocpp transactions export|g' \
  -e 's|.venv/bin/python manage.py ocpp_replay|.venv/bin/python manage.py ocpp trace replay|g' \
  path/to/ops-script.sh

# BSD/macOS sed
sed -i '' \
  -e 's|.venv/bin/python manage.py coverage_ocpp16|.venv/bin/python manage.py ocpp coverage --version 1.6|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp201|.venv/bin/python manage.py ocpp coverage --version 2.0.1|g' \
  -e 's|.venv/bin/python manage.py coverage_ocpp21|.venv/bin/python manage.py ocpp coverage --version 2.1|g' \
  -e 's|.venv/bin/python manage.py import_transactions|.venv/bin/python manage.py ocpp transactions import|g' \
  -e 's|.venv/bin/python manage.py export_transactions|.venv/bin/python manage.py ocpp transactions export|g' \
  -e 's|.venv/bin/python manage.py ocpp_replay|.venv/bin/python manage.py ocpp trace replay|g' \
  path/to/ops-script.sh
```
