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


def test_upgradeability_replays_main_state_through_real_upgrade() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Upgradeability" in text
    assert "needs: installability" in text
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in text
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha }}" in text
    assert "name: Build existing main database state" in text
    assert "python manage.py migrate --noinput --database default" in text
    assert "name: Run real upgrade path" in text
    assert "./upgrade.sh --local --no-restart" in text


def test_upgradeability_is_hosted_and_requires_upgrade_to_apply_migrations() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    upgradeability = text.split("  upgradeability:\n", 1)[1]

    assert "runs-on: ubuntu-latest" in upgradeability
    assert "self-hosted" not in upgradeability
    assert "python manage.py migrate --plan --database default" in upgradeability
    assert (
        upgradeability.count("python manage.py migrate --noinput --database default")
        == 1
    )
    assert "python manage.py check" in upgradeability
    assert "python scripts/check_editable_install_import.py" in upgradeability
    assert "python scripts/check_import_resolution.py" in upgradeability
