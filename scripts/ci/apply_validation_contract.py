from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIMULATOR = ROOT / ".github/workflows/release-simulator.yml"
GENERATED = ROOT / "tmp/release-simulator.generated.yml"
REGRESSIONS = ROOT / "apps/core/tests/reports/release_publish_regressions.py"
TRIGGERS = ROOT / "apps/core/tests/reports/test_ci_trigger_policy.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def update_simulator() -> None:
    text = SIMULATOR.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "      upgrade_replay_summary: ${{ steps.evaluate.outputs.upgrade_replay_summary }}\n",
        "      upgrade_replay_summary: ${{ steps.evaluate.outputs.upgrade_replay_summary }}\n"
        "      support_matrix_summary: ${{ steps.evaluate.outputs.support_matrix_summary }}\n"
        "      live_integration_summary: ${{ steps.evaluate.outputs.live_integration_summary }}\n",
        label="evaluate outputs",
    )
    text = replace_once(
        text,
        "            let upgradeReplaySummary = '- Status: `not evaluated`. Release readiness did not inspect the current default-branch head.';\n",
        "            let upgradeReplaySummary = '- Status: `not evaluated`. Release readiness did not inspect the current default-branch head.';\n"
        "            let supportMatrixSummary = '- Status: `not evaluated`.';\n"
        "            let liveIntegrationSummary = '- Status: `not evaluated`.';\n",
        label="initial health summaries",
    )

    # Canonical operator-facing name; internal artifact/function identifiers remain compatible.
    text = text.replace("Release Upgrade Replay", "Upgrade Health")
    text = text.replace("Release upgrade replay", "Upgrade Health")
    text = text.replace("release upgrade replay", "Upgrade Health")

    start_marker = "              const installHealthRunsForHead = ciRuns\n"
    end_marker = "            } else {\n              core.info(`Skipping release readiness check: ${skipReason}`);\n"
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    replacement = """              function workflowHealthEvidence(workflowName) {
                const runsForHead = ciRuns
                  .filter((run) => run.name === workflowName && run.head_sha === defaultBranchSha)
                  .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
                const latestRun = runsForHead[0];
                if (!latestRun) {
                  return {
                    blockingMessage: `${workflowName} has not run for current ${defaultBranch} commit ${defaultBranchSha}.`,
                    summary: [
                      '- Status: `missing`.',
                      `- Candidate commit: \`${defaultBranchSha}\`.`,
                    ].join('\\n'),
                  };
                }
                const state = latestRun.status === 'completed'
                  ? (latestRun.conclusion || 'unknown')
                  : (latestRun.status || 'unknown');
                const summary = [
                  `- Status: \`${state}\`.`,
                  `- Workflow run: ${latestRun.html_url}`,
                  `- Candidate commit: \`${defaultBranchSha}\`.`,
                ].join('\\n');
                if (latestRun.status !== 'completed') {
                  return {
                    blockingMessage: `${workflowName} for current ${defaultBranch} commit ${defaultBranchSha} is ${latestRun.status}: ${latestRun.html_url}`,
                    summary,
                  };
                }
                if (latestRun.conclusion !== 'success') {
                  return {
                    blockingMessage: `${workflowName} for current ${defaultBranch} commit ${defaultBranchSha} completed with ${latestRun.conclusion || 'unknown'}: ${latestRun.html_url}`,
                    summary,
                  };
                }
                return { blockingMessage: '', summary };
              }

              const supportMatrixEvidence = workflowHealthEvidence('Support Matrix');
              supportMatrixSummary = supportMatrixEvidence.summary;
              if (supportMatrixEvidence.blockingMessage) {
                blockers.push(supportMatrixEvidence.blockingMessage);
              }

              const liveIntegrationEvidence = workflowHealthEvidence('Live Integration');
              liveIntegrationSummary = liveIntegrationEvidence.summary;
              if (liveIntegrationEvidence.blockingMessage) {
                blockers.push(liveIntegrationEvidence.blockingMessage);
              }

"""
    text = text[:start] + replacement + text[end:]

    text = replace_once(
        text,
        "            core.setOutput('upgrade_replay_summary', upgradeReplaySummary);\n",
        "            core.setOutput('upgrade_replay_summary', upgradeReplaySummary);\n"
        "            core.setOutput('support_matrix_summary', supportMatrixSummary);\n"
        "            core.setOutput('live_integration_summary', liveIntegrationSummary);\n",
        label="health outputs",
    )

    text = replace_once(
        text,
        "          UPGRADE_REPLAY_SUMMARY: ${{ needs.evaluate.outputs.upgrade_replay_summary }}\n",
        "          UPGRADE_REPLAY_SUMMARY: ${{ needs.evaluate.outputs.upgrade_replay_summary }}\n"
        "          SUPPORT_MATRIX_SUMMARY: ${{ needs.evaluate.outputs.support_matrix_summary }}\n"
        "          LIVE_INTEGRATION_SUMMARY: ${{ needs.evaluate.outputs.live_integration_summary }}\n",
        label="report env",
    )

    fallback = """            const upgradeReplaySummary = process.env.UPGRADE_REPLAY_SUMMARY || [
              '- Status: unavailable.',
              '- Blocking: yes. Release readiness requires a successful latest-release-to-candidate replay for this commit.'
            ].join('\\n');
"""
    replacement_fallback = (
        fallback
        + """
            const supportMatrixSummary = process.env.SUPPORT_MATRIX_SUMMARY || [
              '- Status: unavailable.',
              '- Blocking: yes. Release readiness requires Support Matrix evidence for this commit.'
            ].join('\\n');
            const liveIntegrationSummary = process.env.LIVE_INTEGRATION_SUMMARY || [
              '- Status: unavailable.',
              '- Blocking: yes. Release readiness requires Live Integration evidence for this commit.'
            ].join('\\n');
"""
    )
    text = replace_once(text, fallback, replacement_fallback, label="report fallbacks")

    text = replace_once(
        text,
        "                upgradeReplaySummary,\n                changesSinceRelease,\n",
        "                upgradeReplaySummary,\n"
        "                supportMatrixSummary,\n"
        "                liveIntegrationSummary,\n"
        "                changesSinceRelease,\n",
        label="report fingerprint",
    )

    text = replace_once(
        text,
        "              '## Upgrade Health',\n              upgradeReplaySummary,\n",
        "              '## Support Matrix',\n"
        "              supportMatrixSummary,\n"
        "              '',\n"
        "              '## Live Integration',\n"
        "              liveIntegrationSummary,\n"
        "              '',\n"
        "              '## Upgrade Health',\n"
        "              upgradeReplaySummary,\n",
        label="report health sections",
    )

    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    GENERATED.write_text(text, encoding="utf-8")


def update_regressions() -> None:
    text = REGRESSIONS.read_text(encoding="utf-8")

    start = text.index(
        "def test_install_health_workflow_is_manual_only_not_scheduled() -> None:\n"
    )
    end = text.index("\n\n@pytest.mark.parametrize(\n", start)
    support_test = """def test_support_matrix_tracks_supported_main_environments() -> None:
    workflow = _workflow_data("install-health.yml")
    on_section = _workflow_on(workflow)

    assert workflow["name"] == "Support Matrix"
    assert "pull_request" not in on_section
    assert on_section["push"]["branches"] == ["main"]
    assert "schedule" not in on_section
    assert "workflow_dispatch" in on_section

    install_job = workflow["jobs"]["install"]
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
        ("debian", "debian:13-slim", "3.13", "sqlite", "ocpp", "apps/ocpp/tests", True),
        (
            "debian",
            "debian:13-slim",
            "3.13",
            "sqlite",
            "extra",
            "--ignore=apps/ocpp/tests",
            True,
        ),
        ("ubuntu22", "ubuntu:22.04", "3.13", "postgres", "smoke", "", False),
    ]

    assert install_job["name"] == (
        "${{ matrix.os_flavor }} / py${{ matrix.python_version }} / "
        "${{ matrix.db_backend }} / ${{ matrix.test_shard }}"
    )
    install_checkout_step = next(
        step
        for step in install_job["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert install_checkout_step["uses"].count("@") == 1
    assert "@v" not in install_checkout_step["uses"]
    assert install_checkout_step["with"]["persist-credentials"] is False
    assert _workflow_step(install_job, "Start native Redis")["run"].strip() == (
        "./scripts/ci/start-native-redis.sh"
    )
    start_postgres_step = _workflow_step(install_job, "Start native PostgreSQL")
    assert start_postgres_step["if"] == "${{ matrix.db_backend == 'postgres' }}"
    assert start_postgres_step["run"].strip() == "./scripts/ci/start-native-postgres.sh"

    detect_xdist_step = _workflow_step(install_job, "Detect pytest xdist arguments")
    assert detect_xdist_step["if"] == "${{ matrix.full_pytest }}"
    run_pytest_step = _workflow_step(install_job, "Run support matrix pytest shard")
    assert run_pytest_step["if"] == "${{ matrix.full_pytest }}"
    assert "--durations=25" in run_pytest_step["run"]

    upload_step = _workflow_step(install_job, "Upload pytest log")
    assert upload_step["if"] == "${{ always() && matrix.full_pytest }}"
    assert upload_step["with"]["name"] == (
        "support-matrix-pytest-results-${{ matrix.os_flavor }}-"
        "${{ matrix.db_backend }}-${{ matrix.test_shard }}"
    )
    assert "notify_failure" in workflow["jobs"]
    assert "notify_recovery" in workflow["jobs"]
"""
    text = text[:start] + support_test + text[end:]

    text = text.replace(
        '        ("install-health.yml", "install"),\n',
        "",
        1,
    )

    start = text.index(
        "def test_pr_ci_uses_hosted_install_and_upgrade_gates() -> None:\n"
    )
    end = text.index(
        "\n\ndef test_linux_sanity_refreshes_cached_virtualenv_before_checks()", start
    )
    pr_test = """def test_pr_ci_uses_hosted_clean_install_gate() -> None:
    workflow = _workflow_data("ci.yml")
    on_section = _workflow_on(workflow)

    assert workflow["name"] == "PR Validation"
    assert list(workflow["jobs"]) == ["python", "clean-install"]
    assert on_section["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    ]
    assert on_section["pull_request"]["paths"] == ["**"]
    assert "workflow_dispatch" in on_section

    python_job = workflow["jobs"]["python"]
    clean_install = workflow["jobs"]["clean-install"]
    assert python_job["uses"] == "arthexis/ci-base/.github/workflows/python-ci.yml@v1"
    assert clean_install["needs"] == "python"
    assert clean_install["name"] == "Clean Install"
    assert clean_install["runs-on"] == "ubuntu-latest"
    assert "self-hosted" not in str(workflow["jobs"])
    assert "upgradeability" not in workflow["jobs"]
"""
    text = text[:start] + pr_test + text[end:]

    text = text.replace(
        'installability = workflow["jobs"]["installability"]',
        'clean_install = workflow["jobs"]["clean-install"]',
    )
    text = text.replace(
        'for step in installability["steps"]', 'for step in clean_install["steps"]'
    )
    text = text.replace("Release Upgrade Replay", "Upgrade Health")
    text = text.replace("Resolve replay refs", "Resolve upgrade refs")
    text = text.replace(
        "Run release upgrade regression tests", "Run upgrade regression tests"
    )
    text = text.replace(
        "Write replay result marker", "Write Upgrade Health result marker"
    )
    text = text.replace("Upload replay artifacts", "Upload Upgrade Health artifacts")
    text = text.replace(
        "requiredWorkflowNames = ['Install Health Check']",
        "requiredWorkflowNames = ['Support Matrix', 'Live Integration']",
    )
    text = text.replace("release_upgrade_replay_active", "upgrade_health_active")
    text = text.replace(
        "release_upgrade_replay_artifact_exists", "upgrade_health_artifact_exists"
    )

    REGRESSIONS.write_text(text, encoding="utf-8")


def update_trigger_policy() -> None:
    text = TRIGGERS.read_text(encoding="utf-8")
    text = text.replace(
        "test_pr_ci_and_install_health_split_pr_and_main_ownership",
        "test_pr_ci_and_support_matrix_split_pr_and_main_ownership",
    )
    text = text.replace("install_health_on", "support_matrix_on")
    TRIGGERS.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    update_simulator()
    update_regressions()
    update_trigger_policy()
