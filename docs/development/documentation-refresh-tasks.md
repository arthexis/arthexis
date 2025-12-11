# Documentation Refresh Tasks

## 1. Refresh the settings maintenance proposal
- **Why it is outdated:** The "Modularize and Test Settings Helpers" entry still claims the helpers live inside `config/settings.py`, even though the module now imports them from `config/settings_helpers.py` and unit tests cover the helper behaviours directly.【F:docs/development/maintenance-roadmap.md†L3-L8】【F:config/settings.py†L31-L42】【F:config/settings_helpers.py†L1-L109】【F:tests/test_settings_helpers.py†L1-L70】
- **Suggested tasks:**
  - Reword the entry to describe the current helper module layout and call out any remaining pain points (for example the import-time monkeypatch or inline hostname discovery helpers) so the roadmap reflects today's architecture instead of past work.【F:config/settings.py†L31-L120】
  - Update the proposed actions to focus on the next improvements—such as adding integration coverage for the monkeypatched validator or moving the hostname parsing helpers into the reusable module—rather than re-splitting code that already lives outside `settings.py`.【F:config/settings.py†L90-L164】【F:config/settings_helpers.py†L70-L109】

## 2. Expand the OCPP manual to cover new flows
- **Why it is outdated:** The OCPP user manual documents inbound charge point calls and a handful of CSMS-initiated actions but stops short of newer flows such as `GetConfiguration` diagnostics, TriggerMessage follow-ups, and the timeout handling that the consumer and store now implement.【F:docs/development/ocpp-user-manual.md†L5-L60】【F:ocpp/consumers.py†L674-L826】【F:ocpp/admin.py†L377-L405】【F:apps/ocpp/views/actions.py†L661-L713】【F:ocpp/store.py†L297-L338】
- **Suggested tasks:**
  - Add sections describing how the admin `GetConfiguration` helper sends requests, logs responses, and surfaces unknown-key metadata so operators know how to interpret those records.【F:ocpp/admin.py†L377-L405】【F:ocpp/consumers.py†L723-L740】
  - Document the TriggerMessage workflow end-to-end, including how follow-up messages are queued, how the consumer logs results, and how the store matches follow-up responses so that support staff can debug reconnection diagnostics.【F:apps/ocpp/views/actions.py†L661-L713】【F:ocpp/consumers.py†L741-L759】【F:ocpp/store.py†L297-L338】
  - Extend the manual's response-handling section to explain the timeout notifications and metadata recorded by `_handle_call_error`, ensuring readers understand how rejected or expired remote calls surface in the UI and logs.【F:ocpp/consumers.py†L827-L906】【F:ocpp/store.py†L280-L338】
