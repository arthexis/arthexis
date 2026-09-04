import pytest

from .release_publish_regressions import *  # noqa: F403
from .release_publish_regressions import _workflow_data, _workflow_on, _workflow_step

# Replace the retired policy assertions while keeping the existing regression
# corpus collected from release_publish_regressions.py under the stable test
# module path.
globals().pop("test_install_health_workflow_is_manual_only_not_scheduled", None)
globals().pop("test_host_redis_workflows_use_native_service", None)


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

    assert "pr_affected_linux_install" not in workflow["jobs"]
    assert "notify_failure" not in workflow["jobs"]
    assert "notify_recovery" not in workflow["jobs"]


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
