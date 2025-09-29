# Maintenance Improvement Proposals

## 1. Modularize and Test Settings Helpers
- **Current state:** `config/settings.py` is a 700+ line module that mixes Django defaults with bespoke helpers such as `_validate_host_with_subnets` and `_load_secret_key`, and it monkeypatches `django.http.request.validate_host` in place. 【F:config/settings.py†L13-L90】【F:config/settings.py†L103-L150】
- **Proposed actions:**
  - Extract the helper functions and monkeypatch into a dedicated module (for example `config/hosts.py`) that can be imported from settings.
  - Add targeted unit tests that exercise IPv4/IPv6 subnet handling and secret-key persistence outside of the Django settings import path.
  - Slim the remaining settings file to focus on declarative configuration, improving readability and making future upgrades less risky.

## 2. Separate Runtime and Tooling Dependencies
- **Current state:** `pyproject.toml` lists developer tooling (e.g. `black`, `twine`, `selenium`) alongside runtime dependencies, so production installs pull in packages that are only needed for development or publishing. 【F:pyproject.toml†L1-L40】
- **Proposed actions:**
  - Move formatting, release, and UI automation dependencies into optional extras such as `[project.optional-dependencies.dev]` and `[project.optional-dependencies.release]`.
  - Add documentation to `docs/development/` describing which extras to install for each workflow, and update CI to use the appropriate extra set.
  - Introduce a `requirements.txt` shim or `pip install .[dev]` guidance for contributors to keep the installation footprint predictable.

## 3. Establish a Single Source of Truth for Node Roles
- **Current state:** Node role names (Terminal, Control, Satellite, Constellation) are duplicated across Python modules and shell scripts, making it easy for the definitions to drift. 【F:config/settings.py†L123-L132】【F:config/celery.py†L9-L24】【F:install.sh†L200-L239】【F:switch-role.sh†L84-L144】
- **Proposed actions:**
  - Create a shared constants module (e.g. `core/node_roles.py`) that exposes an enum or typed mapping of supported roles for Python code, and generate a small sourced shell script (e.g. via `scripts/export-node-roles.sh`) to expose the same list to shell utilities.
  - Update settings, Celery initialization, and management commands to consume the centralized constants.
  - Refactor installation and role-switch shell scripts to read the shared list (for example by sourcing the generated shell script), reducing duplication and ensuring new roles propagate consistently.
