from importlib import import_module
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from apps.core.system import lifecycle


def test_layout_defaults_to_opt(monkeypatch):
    monkeypatch.delenv("ARTHEXIS_INSTALL_ROOT", raising=False)
    monkeypatch.delenv("ARTHEXIS_MANAGED_ROOT", raising=False)

    current = lifecycle.layout()

    assert current.root == Path("/opt/arthexis")
    assert current.checkout == Path("/opt/arthexis/app")


def test_layout_accepts_install_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTHEXIS_INSTALL_ROOT", str(tmp_path))

    current = lifecycle.layout()

    assert current.root == tmp_path
    assert current.checkout == tmp_path / "app"


def test_layout_accepts_legacy_environment_override(monkeypatch, tmp_path):
    monkeypatch.delenv("ARTHEXIS_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("ARTHEXIS_MANAGED_ROOT", str(tmp_path))

    current = lifecycle.layout()

    assert current.root == tmp_path


def test_install_root_override_precedes_legacy_override(monkeypatch, tmp_path):
    legacy_root = tmp_path / "legacy"
    install_root = tmp_path / "install"
    monkeypatch.setenv("ARTHEXIS_MANAGED_ROOT", str(legacy_root))
    monkeypatch.setenv("ARTHEXIS_INSTALL_ROOT", str(install_root))

    current = lifecycle.layout()

    assert current.root == install_root


def test_prepare_runs_application_owned_steps(monkeypatch, tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
    )
    current.checkout.mkdir()
    calls = []

    monkeypatch.setattr(
        lifecycle,
        "migrate",
        lambda *, layout: calls.append("migrate"),
    )
    monkeypatch.setattr(
        lifecycle,
        "ensure_local_node",
        lambda *, layout: calls.append("ensure_local_node"),
    )
    monkeypatch.setattr(
        lifecycle,
        "collectstatic",
        lambda *, layout: calls.append("collectstatic"),
    )

    result = lifecycle.prepare(layout=current)

    assert result == current
    assert calls == ["migrate", "ensure_local_node", "collectstatic"]


def test_prepare_requires_checkout(tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "missing",
    )

    with pytest.raises(FileNotFoundError):
        lifecycle.prepare(layout=current)


def test_prepare_runtime_state_is_idempotent_and_preserves_managed_database(tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
    )
    current.checkout.mkdir()
    legacy_database = current.checkout / "db.sqlite3"
    legacy_database.write_text("legacy", encoding="utf-8")

    first_state = lifecycle._prepare_runtime_state(current)
    managed_database = tmp_path / "var" / "lib" / "db.sqlite3"
    assert managed_database.read_text(encoding="utf-8") == "legacy"

    managed_database.write_text("persistent", encoding="utf-8")
    legacy_database.write_text("changed-checkout", encoding="utf-8")
    second_state = lifecycle._prepare_runtime_state(current)

    assert second_state == first_state == tmp_path / "var"
    assert managed_database.read_text(encoding="utf-8") == "persistent"
    for directory in ("lib", "log", "cache", "run"):
        assert (tmp_path / "var" / directory).is_dir()


def test_install_and_upgrade_record_ownership_after_prepare(monkeypatch, tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
    )
    current.checkout.mkdir()
    calls = []

    def fake_prepare(*, layout, **_kwargs):
        calls.append(("prepare", layout))
        return layout

    def fake_record(root, *, checkout_name):
        calls.append(("record", root, checkout_name))

    monkeypatch.setattr(lifecycle, "prepare", fake_prepare)
    monkeypatch.setattr(lifecycle, "record_managed_installation", fake_record)

    assert lifecycle.install(layout=current) is current
    assert lifecycle.upgrade(layout=current) is current
    assert calls == [
        ("prepare", current),
        ("record", current.root, current.checkout.name),
        ("prepare", current),
        ("record", current.root, current.checkout.name),
    ]


def test_repeated_install_preserves_role_and_ownership_identity(monkeypatch, tmp_path):
    current = lifecycle.InstallationLayout(
        root=tmp_path,
        checkout=tmp_path / "app",
    )
    current.checkout.mkdir()

    monkeypatch.setattr(lifecycle, "prepare", lambda *, layout: layout)
    monkeypatch.setattr(lifecycle, "_warn_if_local_redis_missing", lambda _role: None)

    first = lifecycle.install("--role", "Terminal", layout=current)
    metadata = tmp_path / ".gway" / "arthexis.json"
    first_metadata = metadata.read_text(encoding="utf-8")

    second = lifecycle.install(layout=current)
    second_metadata = metadata.read_text(encoding="utf-8")

    assert first is current
    assert second is current
    assert lifecycle._current_role(current) == "Terminal"
    assert second_metadata == first_metadata


def test_gway_manifest_declares_install_layout_and_importable_lifecycle_hooks():
    repository_root = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads((repository_root / "gway.toml").read_text())

    install = manifest["install"]
    assert install["root"] == "/opt/arthexis"
    assert install["checkout"] == "app"
    assert install["environment"] == ".venv"

    hooks = manifest["lifecycle"]
    assert hooks == {
        "install": "apps.core.system.lifecycle:install",
        "upgrade": "apps.core.system.lifecycle:upgrade",
    }

    for target in hooks.values():
        module_name, function_name = target.split(":", 1)
        function = getattr(import_module(module_name), function_name)
        assert callable(function)


def test_gway_manifest_declares_role_aware_service_topology():
    repository_root = Path(__file__).resolve().parents[3]
    manifest = tomllib.loads((repository_root / "gway.toml").read_text())
    services = manifest["services"]

    assert set(services) == {"web-local", "web-edge", "worker", "beat"}
    assert services["web-local"]["profiles"] == ["Terminal", "Watchtower"]
    assert services["web-edge"]["profiles"] == ["Control", "Satellite"]
    assert services["worker"]["profiles"] == ["Control", "Satellite", "Watchtower"]
    assert services["beat"]["profiles"] == ["Control", "Satellite", "Watchtower"]

    for service in services.values():
        assert service["command"][0] == "{python}"
        assert service["working_directory"] == "{project}"
