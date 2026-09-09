from pathlib import Path

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
    monkeypatch.setattr(lifecycle, "collectstatic", lambda **kwargs: None)

    lifecycle.install("--role", requested, layout=selected)

    assert observed == [expected]
    assert (checkout / ".locks" / "role.lck").read_text() == f"{expected}\n"


def test_upgrade_without_role_preserves_existing_role(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "app"
    role_lock = checkout / ".locks" / "role.lck"
    role_lock.parent.mkdir(parents=True)
    role_lock.write_text("Satellite\n")
    selected = lifecycle.InstallationLayout(root=tmp_path, checkout=checkout)
    monkeypatch.setattr(lifecycle, "migrate", lambda **kwargs: None)
    monkeypatch.setattr(lifecycle, "collectstatic", lambda **kwargs: None)

    lifecycle.upgrade(layout=selected)

    assert role_lock.read_text() == "Satellite\n"


def test_invalid_role_is_rejected() -> None:
    with pytest.raises(SystemExit):
        lifecycle._parse_lifecycle_arguments(("--role", "Database"))


def test_unknown_lifecycle_argument_is_rejected() -> None:
    with pytest.raises(SystemExit):
        lifecycle._parse_lifecycle_arguments(("--celery",))
