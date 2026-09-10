from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "scripts" / "ci" / "linux-sanity.sh"


def test_pull_requests_use_lightweight_linux_sanity_mode() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'if [[ "${GITHUB_EVENT_NAME}" == "pull_request" ]]; then' in workflow
    assert "./scripts/ci/linux-sanity.sh --pr" in workflow
    assert "./scripts/ci/linux-sanity.sh" in workflow


def test_pr_mode_does_not_run_full_install_ci_or_package_checks() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    pr_block = script.split('if [[ "$MODE" == "--pr" ]]; then', 1)[1].split(
        'elif [[ -n "$MODE" ]]', 1
    )[0]

    assert "install-linux-smoke.sh" not in pr_block
    assert "requirements-ci.txt" not in pr_block
    assert "pip wheel" not in pr_block
    assert "twine check" not in pr_block

    assert "sort_pyproject_deps.py --check" in pr_block
    assert "generate_requirements.py --check" in pr_block
    assert "compileall" in pr_block


def test_full_sanity_path_keeps_install_and_package_validation() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "install-linux-smoke.sh" in script
    assert "requirements-ci.txt" in script
    assert "pip wheel --no-deps" in script
    assert "twine check" in script


def test_gway_adapter_is_not_run_for_pull_requests() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    marker = "- name: Validate GWAY Django adapter"
    adapter_block = workflow.split(marker, 1)[1].split("- name:", 1)[0]

    assert "if: github.event_name != 'pull_request'" in adapter_block
