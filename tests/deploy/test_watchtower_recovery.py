from pathlib import Path


def test_watchtower_recovery_is_diagnostics_only() -> None:
    workflow = Path(".github/workflows/watchtower-recovery.yml").read_text(
        encoding="utf-8"
    )

    for forbidden in (
        "cleanup-legacy",
        "confirm_cleanup",
        "redeploy",
        "gway uninstall",
        "install . --system --force",
        "systemctl status",
        "journalctl",
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
