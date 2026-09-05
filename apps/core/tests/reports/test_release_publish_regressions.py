import pytest

from .release_publish_regressions import *  # noqa: F403
from .release_publish_regressions import (
    _workflow_data,
    _workflow_files,
    _workflow_on,
    _workflow_step,
)

# Replace policy assertions while keeping the existing regression corpus collected
# from release_publish_regressions.py under the stable test module path.
globals().pop("test_install_health_workflow_is_manual_only_not_scheduled", None)
globals().pop("test_host_redis_workflows_use_native_service", None)
globals().pop("test_linux_ci_and_security_scans_run_on_pull_requests", None)


def test_tag_from_version_workflow_is_manual_and_dispatches_publish() -> None:
    """Keep release-tag creation manual-only while preserving publish dispatch."""

    workflow = _workflow_data("tag-from-version.yml")
    on_section = _workflow_on(workflow)

    assert isinstance(on_section, dict)
    assert set(on_section) == {"workflow_dispatch"}
    assert workflow["permissions"]["contents"] == "write"
    assert workflow["permissions"]["actions"] == "write"
    assert workflow["concurrency"]["cancel-in-progress"] is False

    job = workflow["jobs"]["tag-from-version"]

    create_tag_run = _workflow_step(job, "Create tag when missing")["run"]
    assert 'tag="v${VERSION}"' in create_tag_run
    assert 'git tag -a "$tag" -m "Release ${tag}"' in create_tag_run
    assert 'git push origin "$tag"' in create_tag_run
    assert "created=false" in create_tag_run
    assert "publish=true" in create_tag_run

    dispatch_step = _workflow_step(job, "Dispatch publish workflow for release tag")
    assert dispatch_step["if"] == "steps.create_tag.outputs.publish == 'true'"
    dispatch_run = dispatch_step["run"]
    assert 'tag="v${VERSION}"' in dispatch_run
    assert (
        'gh workflow run publish.yml --ref "$tag" -f release_tag="$tag"' in dispatch_run
    )


def test_linux_ci_and_security_scans_run_on_pull_requests() -> None:
    pr_workflows: list[str] = []
    for workflow_path in _workflow_files():
        workflow = _workflow_data(workflow_path.name)
        on_section = _workflow_on(workflow)
        if isinstance(on_section, dict) and (
            "pull_request" in on_section or "pull_request_target" in on_section
        ):
            pr_workflows.append(workflow_path.name)

    assert pr_workflows == [
        "ci.yml",
        "codeql.yml",
        "public-release-audit.yml",
        "secret-scan.yml",
    ]


def test_install_health_workflow_runs_on_main_and_manual_dispatch() -> None:
    workflow = _workflow_data("install-health.yml")
    on_section = _workflow_on(workflow)

    assert "pull_request" not in on_section
    assert on_section["push"]["branches"] == ["main"]
    assert "schedule" not in on_section
    assert "workflow_dispatch" in on_section

    install_job = workflow["jobs"]["install"]
    assert "if" not in install_job
    assert install_job["runs-on"] == "ubuntu-latest"
    assert install_job["container"]["image"] == "${{ matrix.container_image }}"
    assert "services" not in install_job
    assert install_job["env"]["OCPP_STATE_REDIS_URL"] == "redis://localhost:6379"
    assert install_job["env"]["REDIS_HOST"] == "127.0.0.1"
    assert install_job["env"]["POSTGRES_HOST"] == "127.0.0.1"

    matrix_entries = install_job["strategy"]["matrix"]["include"]
    assert [
        (
            entry["os_flavor"],
            entry["container_image"],
            entry["python_version"],
            entry["db_backend"],
            entry["test_shard"],
            entry["pytest_args"],
            entry["full_pytest"],
        )
        for entry in matrix_entries
    ] == [
        (
            "debian",
            "debian:13-slim",
            "3.13",
            "sqlite",
            "ocpp",
            "apps/ocpp/tests",
            True,
        ),
        (
            "debian",
            "debian:13-slim",
            "3.13",
            "sqlite",
            "rest",
            "--ignore=apps/ocpp/tests",
            True,
        ),
        (
            "debian",
            "debian:13-slim",
            "3.13",
            "postgres",
            "smoke",
            "",
            False,
        ),
        (
            "ubuntu",
            "ubuntu:24.04",
            "3.13",
            "sqlite",
            "smoke",
            "",
            False,
        ),
        (
            "ubuntu",
            "ubuntu:24.04",
            "3.11",
            "sqlite",
            "smoke",
            "",
            False,
        ),
    ]

    assert (
        install_job["name"]
        == "install (${{ matrix.os_flavor }}, py${{ matrix.python_version }}, ${{ matrix.db_backend }}, ${{ matrix.test_shard }})"
    )
    install_checkout_step = next(
        step
        for step in install_job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert install_checkout_step["uses"].count("@") == 1
    assert "@v" not in install_checkout_step["uses"]
    assert install_checkout_step["with"]["persist-credentials"] is False

    cache_paths = [
        (step.get("with") or {}).get("path")
        for step in install_job["steps"]
        if step.get("uses", "").startswith("actions/cache@")
    ]
    assert ".venv" not in cache_paths

    install_run = _workflow_step(install_job, "Install suite from clean repository")["run"]
    assert "rm -rf .venv" in install_run
    assert "./install.sh --no-start" in install_run
    assert "--embedded" not in install_run

    assert (
        _workflow_step(install_job, "Start native Redis")["run"].strip()
        == "./scripts/ci/start-native-redis.sh"
    )
    start_postgres_step = _workflow_step(install_job, "Start native PostgreSQL")
    assert start_postgres_step["if"] == "${{ matrix.db_backend == 'postgres' }}"
    assert start_postgres_step["run"].strip() == "./scripts/ci/start-native-postgres.sh"

    detect_xdist_step = _workflow_step(install_job, "Detect pytest xdist arguments")
    assert detect_xdist_step["if"] == "${{ matrix.full_pytest }}"

    run_pytest_step = _workflow_step(install_job, "Run install pytest shard")
    run_pytest_script = run_pytest_step["run"]
    assert run_pytest_step["if"] == "${{ matrix.full_pytest }}"
    assert 'read -r -a target_args <<< "${{ matrix.pytest_args }}"' in run_pytest_script
    assert (
        'python -m pytest "${target_args[@]}" "${xdist_args[@]}"' in run_pytest_script
    )
    assert "--durations=25" in run_pytest_script

    upload_step = _workflow_step(install_job, "Upload pytest log")
    upload_name = upload_step["with"]["name"]
    assert upload_step["if"] == "${{ always() && matrix.full_pytest }}"
    assert (
        upload_name == "install-health-pytest-results-${{ matrix.os_flavor }}-"
        "${{ matrix.db_backend }}-${{ matrix.test_shard }}"
    )

    notify_failure = workflow["jobs"]["notify_failure"]
    assert notify_failure["needs"] == "install"
    assert (
        notify_failure["if"]
        == "${{ always() && needs.install.result == 'failure' && github.ref == 'refs/heads/main' }}"
    )
    assert notify_failure["runs-on"] == "ubuntu-latest"
    assert notify_failure["permissions"]["actions"] == "read"
    assert notify_failure["permissions"]["contents"] == "read"
    assert notify_failure["permissions"]["issues"] == "write"
    assert notify_failure["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert notify_failure["env"]["ISSUE_MARKER"] == "<!-- install-health-failure -->"
    failure_script = _workflow_step(
        notify_failure, "Create or update failure issue"
    )["run"]
    assert "gh api --paginate" in failure_script
    assert "actions/runs/${GITHUB_RUN_ID}/jobs?filter=latest&per_page=100" in failure_script
    assert 'select(.conclusion == "failure")' in failure_script
    assert ".steps[]? | select(.conclusion == \"failure\") | .name" in failure_script
    assert ".html_url" in failure_script
    assert "### Latest failure" in failure_script
    assert "### Failed jobs" in failure_script
    assert "<!-- install-health-failure -->" in failure_script
    assert "gh issue create" in failure_script
    assert "gh issue edit" in failure_script
    assert "gh issue comment" in failure_script

    notify_recovery = workflow["jobs"]["notify_recovery"]
    assert notify_recovery["needs"] == "install"
    assert (
        notify_recovery["if"]
        == "${{ always() && needs.install.result == 'success' && github.ref == 'refs/heads/main' }}"
    )
    assert notify_recovery["runs-on"] == "ubuntu-latest"
    assert notify_recovery["permissions"]["contents"] == "read"
    assert notify_recovery["permissions"]["issues"] == "write"
    assert notify_recovery["env"]["GH_TOKEN"] == "${{ github.token }}"
    recovery_script = _workflow_step(
        notify_recovery, "Close recovered failure issue"
    )["run"]
    assert "gh api --paginate" in recovery_script
    assert "<!-- install-health-failure -->" in recovery_script
    assert "gh issue comment" in recovery_script
    assert "gh issue close" in recovery_script


@pytest.mark.parametrize(
    ("workflow_filename", "job_name"),
    [
        ("publish.yml", "test"),
        ("release-upgrade-replay.yml", "replay"),
    ],
)
def test_host_redis_release_workflows_use_native_service(
    workflow_filename: str, job_name: str
) -> None:
    workflow = _workflow_data(workflow_filename)
    job = workflow["jobs"][job_name]
    env = {**workflow.get("env", {}), **job.get("env", {})}

    assert job["runs-on"] == "ubuntu-latest"
    assert "container" not in job
    assert "services" not in job
    assert env["OCPP_STATE_REDIS_URL"] == "redis://localhost:6379"
    assert (
        _workflow_step(job, "Start native Redis")["run"].strip()
        == "./scripts/ci/start-native-redis.sh"
    )
