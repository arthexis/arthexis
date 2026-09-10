from pathlib import Path

path = Path("apps/core/tests/reports/release_publish_regressions.py")
text = path.read_text(encoding="utf-8")

old_sanity = '''def test_linux_ci_uses_single_sanity_job() -> None:
    workflow = _workflow_data("ci.yml")
    on_section = _workflow_on(workflow)

    assert list(workflow["jobs"]) == ["sanity"]
    assert on_section["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]
    assert "workflow_dispatch" in on_section
    assert "env-refresh.bat" not in on_section["pull_request"]["paths"]
    assert "install.bat" not in on_section["pull_request"]["paths"]

    sanity_job = workflow["jobs"]["sanity"]
    assert sanity_job["name"] == "Linux sanity"
    assert workflow["env"]["ARTHEXIS_SKIP_SANITY_APT"] == "1"
    assert sanity_job["runs-on"] == [
        "self-hosted",
        "Linux",
        "X64",
        "arthexis-ci",
    ]
    assert sanity_job["timeout-minutes"] == 40
    checkout_step = next(
        step
        for step in sanity_job["steps"]
        if step.get("uses") == "actions/checkout@v6"
    )
    checkout_clean = (checkout_step.get("with") or {}).get("clean", True)
    assert checkout_clean is True or (
        isinstance(checkout_clean, str) and checkout_clean.lower() == "true"
    )
    clean_command = _workflow_step(sanity_job, "Clean workspace")["run"].strip()
    assert clean_command == "git clean -ffdx"
    assert ".venv" not in clean_command
    assert not any(
        isinstance(run := step.get("run"), str)
        and ".venv" in run
        and "git clean" in run
        for step in sanity_job["steps"]
    )
    assert (
        _workflow_step(sanity_job, "Run Linux sanity checks")["run"].strip()
        == "./scripts/ci/linux-sanity.sh"
    )
'''

new_sanity = '''def test_pr_ci_uses_hosted_install_and_upgrade_gates() -> None:
    workflow = _workflow_data("ci.yml")
    on_section = _workflow_on(workflow)

    assert list(workflow["jobs"]) == ["python", "installability", "upgradeability"]
    assert on_section["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]
    assert on_section["pull_request"]["paths"] == ["**"]
    assert "workflow_dispatch" in on_section

    python_job = workflow["jobs"]["python"]
    installability = workflow["jobs"]["installability"]
    upgradeability = workflow["jobs"]["upgradeability"]

    assert python_job["uses"] == "arthexis/ci-base/.github/workflows/python-ci.yml@v1"
    assert installability["needs"] == "python"
    assert installability["name"] == "Installability"
    assert installability["runs-on"] == "ubuntu-latest"
    assert upgradeability["needs"] == "installability"
    assert upgradeability["name"] == "Upgradeability"
    assert upgradeability["runs-on"] == "ubuntu-latest"
    assert "self-hosted" not in str(workflow["jobs"])
'''

old_python = '''def test_python_dependency_submission_has_runtime_version() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    python_version = (repo_root / ".python-version").read_text(encoding="utf-8").strip()
    workflow = _workflow_data("ci.yml")
    sanity_job = workflow["jobs"]["sanity"]
    setup_python_steps = [
        step
        for step in sanity_job["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]

    assert python_version == "3.13"
    assert (repo_root / "requirements.txt").is_file()
    assert ".python-version" in _workflow_path_triggers(_workflow_on(workflow))
    assert len(setup_python_steps) == 1
    assert setup_python_steps[0]["with"]["python-version"] == python_version
'''

new_python = '''def test_pr_ci_uses_runtime_python_version() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    python_version = (repo_root / ".python-version").read_text(encoding="utf-8").strip()
    workflow = _workflow_data("ci.yml")
    python_job = workflow["jobs"]["python"]
    installability = workflow["jobs"]["installability"]
    setup_python_steps = [
        step
        for step in installability["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]

    assert python_version == "3.13"
    assert (repo_root / "requirements.txt").is_file()
    assert python_job["with"]["python-version"] == python_version
    assert len(setup_python_steps) == 1
    assert setup_python_steps[0]["with"]["python-version"] == python_version
'''

for old, new, name in (
    (old_sanity, new_sanity, "legacy sanity contract"),
    (old_python, new_python, "legacy Python version contract"),
):
    if old not in text:
        raise SystemExit(f"Could not find {name}; refusing partial rewrite")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
