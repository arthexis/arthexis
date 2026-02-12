# Maintenance Improvement Proposals
## 1. Modularize and Test Settings Helpers
- **Current state:** `config/settings_helpers.py` now centralizes the subnet-aware host validation helpers, IP discovery utilities, and secret-key loader, while `config/settings.py` imports them, applies the monkeypatch at import time, and layers hostname discovery plus CSRF origin normalization inline without integration-level tests protecting that behavior.
- **Proposed actions:**
  - Add integration and middleware-level tests that exercise `install_validate_host_with_subnets` alongside the CSRF origin hooks so regressions in Django's host validation or monkeypatched middleware are caught early.
  - Consolidate the hostname normalization and allowed-host utilities (e.g. `_iter_local_hostnames`, `_host_is_allowed`, `_candidate_origin_tuples`) into the shared helpers so the monkeypatch and hostname handling live behind a single module boundary.
  - Document the split of responsibilities between `config/settings_helpers.py` and `config/settings.py` (monkeypatch, hostname expansion, CSRF origin handling) so future refactors extend the helper module instead of reintroducing inline duplicates.
## 2. Separate Runtime and Tooling Dependencies
- **Current state:** `pyproject.toml` lists developer tooling (e.g. `black`, `twine`, `selenium`) alongside runtime dependencies, so production installs pull in packages that are only needed for development or publishing.
- **Proposed actions:**
  - Move formatting, release, and UI automation dependencies into optional extras such as `[project.optional-dependencies.dev]` and `[project.optional-dependencies.release]`.
  - Add documentation to `docs/development/` describing which extras to install for each workflow, and update CI to use the appropriate extra set.
  - Introduce a `requirements.txt` shim or `pip install .[dev]` guidance for contributors to keep the installation footprint predictable.
## 3. Establish a Single Source of Truth for Node Roles
- **Current state:** Node role names (Terminal, Control, Satellite, Watchtower) are duplicated across Python modules and shell scripts, making it easy for the definitions to drift.
- **Proposed actions:**
  - Create a shared constants module (e.g. `core/node_roles.py`) that exposes an enum or typed mapping of supported roles for Python code, and generate a small sourced shell script (e.g. via `scripts/export-node-roles.sh`) to expose the same list to shell utilities.
  - Update settings, Celery initialization, and management commands to consume the centralized constants.
  - Refactor installation and role-switch shell scripts to read the shared list (for example by sourcing the generated shell script), reducing duplication and ensuring new roles propagate consistently.
