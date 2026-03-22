"""Regression coverage for install-hourly workflow command entrypoints."""

from pathlib import Path


def test_install_hourly_import_contracts_uses_make_target() -> None:
    """Ensure the import-contracts workflow step invokes the repository target."""

    workflow_text = Path(".github/workflows/install-hourly.yml").read_text(
        encoding="utf-8"
    )

    assert "- name: Run import contracts" in workflow_text
    assert "make lint-imports" in workflow_text
