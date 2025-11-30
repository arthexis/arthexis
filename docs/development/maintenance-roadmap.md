# Maintenance Improvement Proposals

## 1. Modularize and Test Settings Helpers
- **Current state:** `config/settings_helpers.py` now hosts the extracted helpers for loading the Django secret key and installing the subnet-aware host validator, while `config/settings.py` still imports them and performs the monkeypatch during module import without dedicated integration coverage. 【F:config/settings_helpers.py†L1-L90】【F:config/settings.py†L31-L74】
- **Proposed actions:**
  - Add integration and middleware-level tests that exercise `install_validate_host_with_subnets` alongside CSRF origin checks to guard against regressions when Django updates its host validation behavior. 【F:config/settings.py†L96-L143】
  - Consolidate duplicated hostname logic (e.g. `_host_is_allowed`, `_iter_local_hostnames`) around the shared helpers so that monkeypatch responsibilities and hostname normalization live in one place. 【F:config/settings.py†L76-L143】
  - Document how the helper module should be extended during future settings refactors so contributors understand which logic belongs in `config/settings_helpers.py` versus the main settings file. 【F:config/settings_helpers.py†L1-L90】【F:config/settings.py†L31-L143】

## 2. Separate Runtime and Tooling Dependencies
- **Current state:** `pyproject.toml` lists developer tooling (e.g. `black`, `twine`, `selenium`) alongside runtime dependencies, so production installs pull in packages that are only needed for development or publishing. 【F:pyproject.toml†L1-L40】
- **Proposed actions:**
  - Move formatting, release, and UI automation dependencies into optional extras such as `[project.optional-dependencies.dev]` and `[project.optional-dependencies.release]`.
  - Add documentation to `docs/development/` describing which extras to install for each workflow, and update CI to use the appropriate extra set.
  - Introduce a `requirements.txt` shim or `pip install .[dev]` guidance for contributors to keep the installation footprint predictable.

## 3. Establish a Single Source of Truth for Node Roles
- **Current state:** Node role names (Terminal, Control, Satellite, Watchtower) are duplicated across Python modules and shell scripts, making it easy for the definitions to drift. 【F:config/settings.py†L123-L132】【F:config/celery.py†L9-L24】【F:install.sh†L200-L239】【F:scripts/switch-role.sh†L84-L144】
- **Proposed actions:**
  - Create a shared constants module (e.g. `core/node_roles.py`) that exposes an enum or typed mapping of supported roles for Python code, and generate a small sourced shell script (e.g. via `scripts/export-node-roles.sh`) to expose the same list to shell utilities.
  - Update settings, Celery initialization, and management commands to consume the centralized constants.
  - Refactor installation and role-switch shell scripts to read the shared list (for example by sourcing the generated shell script), reducing duplication and ensuring new roles propagate consistently.
