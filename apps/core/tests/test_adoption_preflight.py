from pathlib import Path

import pytest

from apps.core.system import adoption, managed_install
from apps.core.system.lifecycle import InstallationLayout


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "manage.py").write_text("# source checkout\n", encoding="utf-8")
    (source / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    locks = source / ".locks"
    locks.mkdir()
    (locks / "role.lck").write_text("Control\n", encoding="utf-8")
    return source


def test_preflight_inventory_is_read_only_and_does_not_expose_env_values(
    monkeypatch, tmp_path
) -> None:
    source = _source(tmp_path)
    database = source / "db.sqlite3"
    database.write_text("developer-state", encoding="utf-8")
    (source / ".venv").mkdir()
    (source / "media").mkdir()
    secret = "DO_NOT_PRINT_THIS_SECRET"
    (source / "production.env").write_text(f"TOKEN={secret}\n", encoding="utf-8")
    target = tmp_path / "managed"

    monkeypatch.setattr(
        adoption,
        "_git_inventory",
        lambda selected, blockers: ("abc123", "feature", True),
    )

    plan = adoption.inspect_adoption(source, target_root=target)

    assert plan["ready"] is True
    assert plan["source"] == str(source.resolve())
    assert plan["target_checkout"] == str((target / "app").resolve())
    assert plan["revision"] == "abc123"
    assert plan["branch"] == "feature"
    assert plan["dirty"] is True
    assert plan["version"] == "1.2.3"
    assert plan["role"] == "Control"
    assert plan["environment_files"] == ["production.env"]
    assert secret not in repr(plan)
    assert database.read_text(encoding="utf-8") == "developer-state"
    assert not target.exists()

    by_kind = {item["kind"]: item for item in plan["transfers"]}
    assert by_kind["database"]["classification"] == "copyable"
    assert by_kind["python-environment"]["classification"] == "regenerable"
    assert by_kind["media"]["classification"] == "copyable"
    assert by_kind["services"]["classification"] == "regenerable"


def test_preflight_reports_existing_managed_target_as_blocker(monkeypatch, tmp_path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "managed"
    (target / "app").mkdir(parents=True)

    monkeypatch.setattr(
        adoption,
        "_git_inventory",
        lambda selected, blockers: ("abc123", "main", False),
    )

    plan = adoption.inspect_adoption(source, target_root=target)

    assert plan["ready"] is False
    assert any("target checkout already exists" in blocker for blocker in plan["blockers"])


def test_managed_install_routes_adoption_dry_run_to_inventory(monkeypatch, tmp_path) -> None:
    source = _source(tmp_path)
    current = InstallationLayout(root=tmp_path / "managed", checkout=tmp_path / "managed" / "app")
    expected = {"mode": "adoption-preflight", "ready": True}
    calls = []

    def fake_inspect(selected, *, target_root):
        calls.append((selected, target_root))
        return expected

    monkeypatch.setattr(managed_install, "inspect_adoption", fake_inspect)

    result = managed_install.install(
        "--adopt",
        "--from",
        str(source),
        "--dry-run",
        layout=current,
    )

    assert result is expected
    assert calls == [(str(source), current.root)]
    assert not current.root.exists()


def test_managed_install_preserves_normal_install_path(monkeypatch, tmp_path) -> None:
    current = InstallationLayout(root=tmp_path, checkout=tmp_path / "app")
    expected = object()
    calls = []

    def fake_install(*arguments, layout=None):
        calls.append((arguments, layout))
        return expected

    monkeypatch.setattr(managed_install.lifecycle, "install", fake_install)

    result = managed_install.install("--role", "Terminal", layout=current)

    assert result is expected
    assert calls == [(('--role', 'Terminal'), current)]


def test_managed_install_refuses_mutating_adoption_for_now(tmp_path) -> None:
    source = _source(tmp_path)

    with pytest.raises(managed_install.AdoptionArgumentError, match="not implemented"):
        managed_install.install("--adopt", "--from", str(source))
