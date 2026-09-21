from unittest.mock import patch

from scripts.retire_legacy_web_service import retire_legacy_web_service


def test_legacy_service_migration_removes_only_web_record() -> None:
    class Record:
        def __init__(self, service, backend="systemd"):
            self.service = service
            self.backend = backend

    state = type("State", (), {"get": lambda self, project: [
        Record("web"),
        Record("arthexis.com"),
        Record("other", backend="process"),
    ]})()
    backend = type("Backend", (), {"uninstall_units": lambda self, *args, **kwargs: calls.append((args, kwargs))})()
    calls = []

    with (
        patch("gway.install.paths.install_paths") as install_paths,
        patch("gway.install.service.ServiceInstallState", return_value=state),
        patch("gway.install.service.get", return_value=backend),
    ):
        install_paths.return_value.root = __import__("pathlib").Path("/var/lib/gway")
        retire_legacy_web_service()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("arthexis",)
    assert [record.service for record in kwargs["records"]] == ["web"]
