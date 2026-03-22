"""Regression coverage for install-hourly workflow command entrypoints."""

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.critical, pytest.mark.regression]


def test_install_hourly_import_contracts_uses_import_linter_cli() -> None:
    """Ensure the install workflow runs Import Linter in the import-contracts step."""

    workflow_path = Path(".github/workflows/install-hourly.yml")
    workflow_data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    install_job = workflow_data.get("jobs", {}).get("install", {})
    steps = install_job.get("steps", [])

    import_contracts_step = next(
        (step for step in steps if step.get("name") == "Run import contracts"),
        None,
    )

    assert import_contracts_step is not None, "Step 'Run import contracts' not found"
    assert "lint-imports" in import_contracts_step.get("run", "")
