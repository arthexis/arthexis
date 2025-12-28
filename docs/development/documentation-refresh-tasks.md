# Documentation Refresh Tasks

## 1. Add installation guidance for dependency extras
- **Why it matters now:** The maintenance roadmap calls for moving development and release tools into optional extras, but the contributor docs still assume a single monolithic dependency set. Without explicit install steps, contributors will not know which extras to apply once the split lands.【F:docs/development/maintenance-roadmap.md†L10-L25】
- **Suggested tasks:**
  - Draft a short guide in `docs/development/` that explains which extras to install for everyday development, release publishing, and UI automation, and link it from the onboarding docs so newcomers do not over-install packages.【F:docs/development/maintenance-roadmap.md†L14-L25】
  - Update CI and local setup references (e.g., `install.sh`) to mention the new extras or point to the guide once the dependency split is implemented.

## 2. Fill CSMS→charge point coverage gaps in the OCPP manual
- **Why it matters now:** The OCPP user manual documents only a subset of outbound CSMS actions (RemoteStart/Stop, Reset, ChangeAvailability, DataTransfer, GetConfiguration, TriggerMessage), but the control endpoint implements many more flows that administrators rely on.【F:docs/development/ocpp-user-manual.md†L60-L131】 ReserveNow, ChangeConfiguration, ClearCache, CancelReservation, UnlockConnector, SendLocalList, GetLocalListVersion, UpdateFirmware, SetChargingProfile, and GetDiagnostics are live in `apps/ocpp/views/actions.py` yet undocumented, leaving operators without reference material.【F:apps/ocpp/views/actions.py†L46-L1265】
- **Suggested tasks:**
  - Add per-action sections to the manual describing payload validation, logging, pending-call tracking, and timeout handling for each of the missing CSMS actions so support teams can trace behaviour without reading code.【F:apps/ocpp/views/actions.py†L46-L1265】【F:apps/ocpp/store.py†L620-L717】
  - Cross-link the new sections to the pending-call lifecycle description already in the manual so readers understand how responses and timeouts surface in the UI.【F:docs/development/ocpp-user-manual.md†L132-L186】
