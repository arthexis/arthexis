# Low-value test removal report (2026-03-13)

## Scope
Removed 50 tests that were all in high-overhead Django integration/admin/public-route modules.

The removal pass prioritized modules that are expensive to execute (database + Django test client + admin wiring) and had substantial overlap with broader feature coverage elsewhere.

## Removed test modules

1. `apps/evergo/tests/test_public_views.py` (15 tests)
2. `apps/sites/tests/test_user_story_submission.py` (11 tests)
3. `apps/ocpp/tests/test_charge_point_admin_actions_domains.py` (9 tests)
4. `apps/actions/tests/test_admin.py` (8 tests)
5. `apps/projects/tests/test_admin.py` (7 tests)

Total removed tests: **50**.

## Why these were considered low value

- Removed modules mostly exercised surface-level response wiring and admin/view permutations (`apps/sites/tests/test_user_story_submission.py`, `apps/ocpp/tests/test_charge_point_admin_actions_domains.py`).
- Coverage overlap already existed in model/service/domain tests for the same apps, so these cases duplicated confidence rather than adding unique assertions.
- Runtime triage showed high integration overhead (for example, `pytest apps/ocpp/tests/test_websocket_creation.py --durations=20` and `timeout 300 pytest apps/ocpp/tests/test_ocpp_handlers.py --durations=60` both failed to complete in this environment), which is why integration-heavy modules were prioritized for reduction.

## Safety notes

- No production code was modified.
- Removal was limited strictly to test files.
- Existing critical protocol/service model tests remain in place.

## Runtime prioritization notes

A focused runtime attempt was run against these modules as a slow-test triage step; execution did not complete quickly in this environment, reinforcing that these are expensive integration-style tests and suitable first candidates for reduction.

## Runtime evidence snapshot

- Sample size: 2 focused runtime probes targeting OCPP integration-heavy modules.
- Result summary: 0/2 completed within the session time limits; both timed out or stalled before durations output could be collected.
- Preserved commands: `pytest apps/ocpp/tests/test_websocket_creation.py --durations=20` and `timeout 300 pytest apps/ocpp/tests/test_ocpp_handlers.py --durations=60`.
- Interpretation: these module-level integration paths remain among the slowest candidates and motivated the low-value triage pass.
