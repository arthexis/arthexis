import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from scripts.retire_legacy_web_service import retire_legacy_web_service


def test_legacy_service_migration_removes_only_web_record() -> None:
    class Record:
        def __init__(self, service, backend="systemd"):
            self.service = service
            self.backend = backend

    state = type(
        "State",
        (),
        {
            "get": lambda self, project: [
                Record("web"),
                Record("arthexis.com"),
                Record("other", backend="process"),
            ]
        },
    )()
    calls = []
    backend = type(
        "Backend",
        (),
        {
            "uninstall_units": lambda self, *args, **kwargs: calls.append(
                (args, kwargs)
            )
        },
    )()

    gway = ModuleType("gway")
    install = ModuleType("gway.install")
    paths = ModuleType("gway.install.paths")
    service = ModuleType("gway.install.service")
    paths.install_paths = lambda system: type(
        "Paths", (), {"root": Path("/var/lib/gway")}
    )()
    service.ServiceInstallState = lambda root: state
    service.get = lambda name: backend

    with patch.dict(
        sys.modules,
        {
            "gway": gway,
            "gway.install": install,
            "gway.install.paths": paths,
            "gway.install.service": service,
        },
    ):
        retire_legacy_web_service()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("arthexis",)
    assert [record.service for record in kwargs["records"]] == ["web"]
