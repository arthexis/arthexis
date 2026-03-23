from apps.sigils import node_features


def test_check_node_feature_returns_none_for_other_slugs():
    result = node_features.check_node_feature("other", node=None)

    assert result is None


def test_check_node_feature_reflects_llvm_runtime(monkeypatch):
    monkeypatch.setattr(node_features, "is_llvm_scanner_runtime_available", lambda: True)

    result = node_features.check_node_feature(node_features.LLVM_SIGILS_SLUG, node=None)

    assert result is True


def test_register_node_feature_detection_registers_slug():
    class Registry:
        def __init__(self):
            self.calls = []

        def register(self, slug, *, check=None, setup=None):
            self.calls.append((slug, check, setup))

    registry = Registry()

    node_features.register_node_feature_detection(registry)

    assert registry.calls == [
        (
            node_features.LLVM_SIGILS_SLUG,
            node_features.check_node_feature,
            node_features.setup_node_feature,
        )
    ]
