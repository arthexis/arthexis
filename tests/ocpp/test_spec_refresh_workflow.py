from pathlib import Path


def test_ocpp_spec_refresh_is_manual_and_github_hosted() -> None:
    workflow = Path(".github/workflows/ocpp-spec-refresh.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "self-hosted" not in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow


def test_ocpp_spec_refresh_does_not_publish_official_archives() -> None:
    workflow = Path(".github/workflows/ocpp-spec-refresh.yml").read_text(
        encoding="utf-8"
    )

    assert "ocpp_spec_refresh.py" in workflow
    assert "Upload compact refresh evidence" in workflow
    assert "*.zip" not in workflow
    assert "archive.zip" not in workflow
