from pathlib import Path


def test_ocpp_conformance_is_manual_and_off_production_runner() -> None:
    workflow = Path(".github/workflows/ocpp-conformance.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "self-hosted" not in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow


def test_ocpp_conformance_prepares_official_schemas_and_runs_heavy_suite() -> None:
    workflow = Path(".github/workflows/ocpp-conformance.yml").read_text(
        encoding="utf-8"
    )

    assert "scripts/ocpp_conformance_prepare.py" in workflow
    assert "jsonschema==4.25.1" in workflow
    assert "OCPP_CONFORMANCE_SCHEMA_ROOT" in workflow
    assert "tests.ocpp.test_official_schema_conformance" in workflow
