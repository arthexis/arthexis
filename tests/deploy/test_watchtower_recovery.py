from pathlib import Path

WORKFLOW = Path(".github/workflows/watchtower-recovery.yml")


def test_watchtower_recovery_keeps_sanitized_diagnostics() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for forbidden in (
        "cleanup-legacy",
        "confirm_cleanup",
        "redeploy",
        "gway uninstall",
        "install . --system --force",
        "ss -ltnp",
        "find /var/lib",
        "ls -l",
        "upload-artifact",
        "watchtower-recovery.txt",
        "python --version",
        "pip check",
    ):
        assert forbidden not in workflow

    for required in (
        "Run sanitized diagnostics",
        "service=active",
        "local_health=ok",
        "public_health=ok",
        "public_root=ok",
        "nginx_config=ok",
        "nginx_service=active",
        "django_check=ok",
        "migration_drift=none",
        "ocpp_matrix=ok",
    ):
        assert required in workflow


def test_recovery_workflow_exposes_diagnose_and_checkpoint_actions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "action:" in workflow
    assert "type: choice" in workflow
    assert "default: diagnose" in workflow
    assert "- diagnose" in workflow
    assert "- checkpoint" in workflow


def test_checkpoint_is_additive_to_sanitized_diagnostics() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    diagnostics = workflow.index("- name: Run sanitized diagnostics")
    checkpoint = workflow.index("- name: Run remote checkpoint")

    assert diagnostics < checkpoint
    assert "if: inputs.action == 'checkpoint'" in workflow


def test_checkpoint_runs_safe_remote_preflight_and_acceptance() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "deploy/remote-preflight.rx" in workflow
    assert 'verify_remote_deployment.py" local' in workflow
    assert 'verify_remote_deployment.py" public' in workflow
    assert 'echo "remote_checkpoint=ok"' in workflow


def test_checkpoint_does_not_issue_credentials_or_supply_cache_env() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "security token create" not in workflow
    assert "GWAY_CACHE_DIR=/var/lib/gway/cache" not in workflow



def test_recovery_cleans_privileged_bytecode_before_checkout() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    cleanup = workflow.index(
        "- name: Clean privileged Python bytecode from runner workspace"
    )
    checkout = workflow.index("- name: Checkout trusted main revision")

    assert cleanup < checkout
    assert (
        'sudo -n find "${GITHUB_WORKSPACE}" -type d -name __pycache__ '
        "-prune -exec rm -rf {} +"
    ) in workflow
    assert "*.pyc" in workflow
    assert "*.pyo" in workflow
