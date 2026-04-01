# EVCS Simulator v2 Proposal (Mobility House OCPP)

## Goal
Build a new EVCS simulator track (`v2`) that is powered by the Mobility House `ocpp` implementation, while keeping the current simulator available as a fallback during migration.

## Why change
The current simulator (`apps/simulators/evcs.py`) manually crafts OCPP frames and embeds protocol logic inline. A Mobility House-based simulator would provide:

- protocol-typed message objects instead of ad-hoc JSON payloads;
- cleaner support for multiple versions (1.6 and 2.0.1/2.1 profiles);
- improved maintainability for remote command handlers and extensibility.

## Proposed architecture

### 1) New proposal module
Add a dedicated proposal module: `apps/simulators/evcs_mobilityhouse.py`.

It introduces:

- `MobilityHouseSimulatorConfig`: explicit simulator runtime contract;
- `build_simulator_proposal(...)`: a dependency-checked proposal builder;
- `MobilityHouseChargePointAdapter`: an integration placeholder for runtime wiring.

### 2) Feature-flagged runtime selection
When implementation work starts, route simulator startup through a flag:

- **default**: existing JSON-frame simulator (`apps.simulators.evcs`);
- **opt-in**: Mobility House adapter path.

This preserves backward compatibility for current operators and test harnesses.

### 3) Scenario plugin strategy
Implement scenarios as async plugins (e.g., basic charging loop, remote stop/reset, smart charging). The adapter should:

- map UI fields (`duration`, `interval`, `repeat`, `delay`) to scenario parameters;
- report state via existing OCPP store logging and simulator state tracking.

### 4) Incremental rollout plan
1. Land proposal module and tests (done in this change).
2. Add optional `ocpp` dependency to environment profile.
3. Implement minimal v1.6 charging scenario with typed actions.
4. Run parallel validation against existing simulator workflows.
5. Promote v2 to default only after parity checks pass.

## Operational notes
- Keep dependency optional initially to avoid disrupting constrained deployments.
- Fail fast with a specific exception when `ocpp` is unavailable.
- Preserve current admin and dashboard UX while runtime internals evolve.
