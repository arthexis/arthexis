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
        "mv \"\${source_path}\"",
        "install . --system --force",
    ):
        assert forbidden not in workflow

    for required in (
        "Capture Watchtower diagnostics",
        "=== managed installation ===",
        "=== durable Arthexis data ===",
        "=== runtime/package ===",
        "=== systemd service ===",
        "=== recent service journal ===",
        "=== listening sockets ===",
        "=== local health ===",
        "=== local root ===",
        "=== public health ===",
        "=== public root ===",
        "=== nginx ===",
        "manage.py ocpp_matrix",
        "journalctl -u arthexis-arthexis.com.service -n 100",
        "nginx -t",
    ):
        assert required in workflow
