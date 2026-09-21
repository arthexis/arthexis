"""One-time Watchtower service identity migration."""

def retire_legacy_web_service() -> None:
    """Remove the old Gway-managed 'web' systemd service, if present."""
    from gway.install.paths import install_paths
    from gway.install.service import ServiceInstallState, get

    paths = install_paths(system=True)
    state_root = paths.root / "services-installed"
    state = ServiceInstallState(state_root)
    records = [
        record
        for record in state.get("arthexis")
        if record.backend == "systemd" and record.service == "web"
    ]
    if not records:
        return
    get("systemd").uninstall_units(
        "arthexis",
        state_root=state_root,
        records=records,
    )


if __name__ == "__main__":
    retire_legacy_web_service()
