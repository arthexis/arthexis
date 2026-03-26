from pathlib import Path

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
