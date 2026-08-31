from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.nodes.feature_detection import NodeFeatureDetectionRegistry


class DummyNode:
    pass


@pytest.fixture
def registry():
    instance = NodeFeatureDetectionRegistry()
    instance.reset()
    return instance


def test_register_rejects_non_canonical_detector_signature(registry):
    def invalid_callback(slug: str, node):
        del slug, node
        return True

    with pytest.raises(TypeError):
        registry.register("demo", check=invalid_callback)


def test_discover_uses_explicit_approved_registry(monkeypatch, registry):
    called = []

    def check_callback(slug: str, *, node, base_dir: Path, base_path: Path):
        del node, base_dir, base_path
        return slug == "demo"

    def registrar(target: NodeFeatureDetectionRegistry) -> None:
        called.append("registrar")
        target.register("demo", check=check_callback)

    monkeypatch.setattr(
        "apps.nodes.feature_registry.APPROVED_NODE_FEATURE_REGISTRARS",
        (registrar,),
    )

    result = registry.detect(
        "demo",
        node=DummyNode(),
        base_dir=Path("."),
        base_path=Path("."),
    )

    assert called == ["registrar"]
    assert result is True


def test_discover_fails_loudly_on_invalid_registry_entry(monkeypatch, registry):
    monkeypatch.setattr(
        "apps.nodes.feature_registry.APPROVED_NODE_FEATURE_REGISTRARS",
        ("not-callable",),
    )

    with pytest.raises(TypeError):
        registry.discover()


def test_iter_approved_node_feature_registrars_skips_uninstalled_optional_apps(
    monkeypatch,
):
    import apps.nodes.feature_registry as feature_registry

    imported_modules: list[str] = []

    def fake_import_module(module_name: str):
        imported_modules.append(module_name)
        return SimpleNamespace(register_node_feature_detection=lambda registry: None)

    monkeypatch.setattr(feature_registry, "APPROVED_NODE_FEATURE_REGISTRARS", ())
    monkeypatch.setattr(
        feature_registry,
        "OPTIONAL_NODE_FEATURE_REGISTRARS",
        (
            ("apps.summary", "apps.summary.node_features"),
            ("apps.docs", "apps.docs.node_features"),
        ),
    )
    monkeypatch.setattr(
        feature_registry.django_apps,
        "is_installed",
        lambda app_config_name: app_config_name == "apps.docs",
    )
    monkeypatch.setattr(feature_registry, "import_module", fake_import_module)

    registrars = list(feature_registry.iter_approved_node_feature_registrars())

    assert len(registrars) == 1
    assert imported_modules == ["apps.docs.node_features"]


def test_discover_keeps_lcd_detector_available_without_screens_app(tmp_path):
    from apps.screens.startup_notifications import LCD_RUNTIME_LOCK_FILE

    registry = NodeFeatureDetectionRegistry()
    registry.reset()
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / LCD_RUNTIME_LOCK_FILE).touch()

    result = registry.detect(
        "lcd-screen",
        node=DummyNode(),
        base_dir=tmp_path,
        base_path=tmp_path,
    )

    assert result is True
