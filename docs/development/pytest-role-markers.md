# Pytest node role markers

Some features ship only on specific node roles (Terminal, Constellation, Control, or Satellite). Tests that cover these features must be annotated with `@pytest.mark.role(<role name>)` so they can be included or excluded during targeted smoke runs.

## When to add role markers

Apply a role marker whenever a test exercises functionality that depends on a role-scoped feature or piece of hardware. Examples include:

- LCD tooling and other kiosk display utilities (`Terminal` and `Control`).
- Control-surface helpers such as the background RFID reader (`Control`).
- Hardware integrations that are bundled with multiple roles (for example the RFID scanner, which ships with all roles).

Tests that cover behaviour shared by every role do not need explicit markers.

Multiple markers may be stacked on the same test (or declared via `pytestmark`) when a component is shared across several roles:

```python
import pytest

pytestmark = [pytest.mark.role("Terminal"), pytest.mark.role("Control")]
```

## Filtering by node role

Pytest already honours the `NODE_ROLE` environment variable by skipping tests whose markers do not include the requested role:

```bash
NODE_ROLE=Terminal pytest tests/test_lcd_smbus2.py
```

Setting the optional `NODE_ROLE_ONLY` flag tightens this filter by skipping any test that lacks a role marker after the per-role filtering has been applied. This is useful for smoke runs that should cover only the role-specific suites and fail fast if new tests forgot to declare their role affinity.

```bash
NODE_ROLE=Control NODE_ROLE_ONLY=1 pytest tests
```

Both environment variables accept common truthy/falsey values (`1`, `true`, `yes`, etc.). Leaving `NODE_ROLE_ONLY` unset (the default) preserves the existing behaviour where unmarked tests always run.
