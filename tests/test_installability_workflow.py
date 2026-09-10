from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_pr_installability_is_github_hosted_and_clean() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Installability" in text
    assert "runs-on: ubuntu-latest" in text
    assert "self-hosted" not in text
    assert "./scripts/ci/install-linux-smoke.sh --cold" in text
    assert "ARTHEXIS_CI_INSTALL_SMOKE_DB_MODE: apply" in text
    assert "python -m pip check" in text


def test_installability_runs_after_shared_python_ci() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "uses: arthexis/ci-base/.github/workflows/python-ci.yml@v1" in text
    assert "python-version: '3.13'" in text
    assert "test-python-versions: '[\"3.13\"]'" in text
    assert "needs: python" in text


def test_installability_exposes_read_only_migration_verification() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Verify Django migrations" in text
    assert "python scripts/check_migration_conflicts.py" in text
    assert "python manage.py migrations check" in text
    assert "python manage.py migrate\n" not in text


def test_pr_installability_does_not_follow_gway_main() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "github.com/arthexis/gway.git@main" not in text
    assert "gway register" not in text
