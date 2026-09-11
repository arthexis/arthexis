from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.core.system import lifecycle


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("Control", "Control"),
        ("Satellite", "Satellite"),
        ("Terminal", "Terminal"),
        ("Watchtower", "Watchtower"),
        ("control", "Control"),
    ],
)
def test_role_argument_is_persisted_before_prepare(
    monkeypatch, tmp_path: Path, requested: str, expected: str
) -> None:
    checkout = tmp_path / "app"
    checkout.mkdir()
    selected = lifecycle.InstallationLayout(root=tmp_path, checkout=checkout)
    observed: list[str] = []

    def fake_migrate(*, layout=None) -> None:
        observed.append((checkout / ".locks" / "role.lck").read_text().strip())

    monkeypatch.setattr(lifecycle, "migrate", fake_migrate)
    monkeypatch.setattr(lifecycle, "ensure_local_node", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "collectstatic", lambda **kwargs: None)

    lifecycle.install("--role", requested, layout=selected)

    assert observed == [expected]
    assert (checkout / ".locks" / "role.lck").read_text() == f"{expected}\n"


def test_upgrade_without_role_preserves_existing_role(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "app"
    checkout.mkdir()
    role_lock = checkout / ".locks" / "role.lck"
    role_lock.parent.mkdir(parents=True)
    role_lock.write_text("Satellite\n")
    selected = lifecycle.InstallationLayout(root=tmp_path, checkout=checkout)
    monkeypatch.setattr(lifecycle, "migrate", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "ensure_local_node", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "collectstatic", lambda **kwargs: None)

    lifecycle.upgrade(layout=selected)

    assert role_lock.read_text() == "Satellite\n"


def test_site_is_configured_after_lifecycle_tasks(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "app"
    checkout.mkdir()
    selected = lifecycle.InstallationLayout(root=tmp_path, checkout=checkout)
    observed: list[str] = []

    monkeypatch.setattr(
        lifecycle, "migrate", lambda **kwargs: observed.append("migrate")
    )
    monkeypatch.setattr(
        lifecycle,
        "ensure_local_node",
        lambda **kwargs: observed.append("node"),
    )
    monkeypatch.setattr(
        lifecycle,
        "collectstatic",
        lambda **kwargs: observed.append("static"),
    )
    monkeypatch.setattr(
        lifecycle,
        "_ensure_gway_web",
        lambda: observed.append("gway-web") or True,
    )
    monkeypatch.setattr(
        lifecycle,
        "configure_site",
        lambda domain=None, **kwargs: observed.append(f"site:{domain}"),
    )

    lifecycle.install("--site", "charge.example.com", layout=selected)

    assert observed == [
        "migrate",
        "node",
        "static",
        "gway-web",
        "site:charge.example.com",
    ]


def test_bare_site_uses_default_site_and_canonical_name(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "app"
    checkout.mkdir()
    selected = lifecycle.InstallationLayout(root=tmp_path, checkout=checkout)
    calls: list[tuple[str, tuple[str, ...]]] = []

    monkeypatch.setattr(
        lifecycle,
        "run_manage",
        lambda command, *arguments, **kwargs: calls.append((command, arguments)),
    )

    lifecycle.configure_site(layout=selected)

    assert calls == [
        ("site", ("--name", "arthexis", "--no-refresh-node")),
    ]


def test_role_and_site_can_be_supplied_together() -> None:
    options = lifecycle._parse_lifecycle_arguments(
        ("--role", "watchtower", "--site", "charge.example.com")
    )

    assert options.role == "Watchtower"
    assert options.site == "charge.example.com"


def test_site_flag_can_omit_domain() -> None:
    options = lifecycle._parse_lifecycle_arguments(("--site",))

    assert options.site == ""


def test_gway_web_is_skipped_when_gway_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(lifecycle.shutil, "which", lambda command: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("subprocess should not run without gway")

    monkeypatch.setattr(lifecycle.subprocess, "run", unexpected)

    assert lifecycle._ensure_gway_web() is False


@pytest.mark.parametrize(
    ("path_returncode", "expected_action"),
    [(0, "upgrade"), (1, "install")],
)
def test_gway_web_is_installed_or_upgraded(
    monkeypatch, path_returncode: int, expected_action: str
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        lifecycle.shutil,
        "which",
        lambda command: "/usr/local/bin/gway",
    )

    def fake_run(arguments, **kwargs):
        calls.append((list(arguments), kwargs))
        if arguments[1:3] == ["path", "web"]:
            return SimpleNamespace(returncode=path_returncode)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)

    assert lifecycle._ensure_gway_web() is True
    assert calls[0][0] == ["/usr/local/bin/gway", "path", "web"]
    assert calls[1] == (
        ["/usr/local/bin/gway", expected_action, "web"],
        {"check": True, "text": True},
    )


def test_invalid_role_is_rejected() -> None:
    with pytest.raises(SystemExit):
        lifecycle._parse_lifecycle_arguments(("--role", "Database"))


def test_unknown_lifecycle_argument_is_rejected() -> None:
    with pytest.raises(SystemExit):
        lifecycle._parse_lifecycle_arguments(("--celery",))
