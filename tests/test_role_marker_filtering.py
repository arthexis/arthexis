from types import SimpleNamespace

import conftest


class DummyItem:
    def __init__(self, roles=None, features=None):
        self._role_markers = list(roles or [])
        self._feature_markers = list(features or [])
        self.added_markers = []

    def iter_markers(self, name):
        if name == "role":
            for role in self._role_markers:
                yield SimpleNamespace(name="role", args=(role,))
        elif name == "feature":
            for feature in self._feature_markers:
                yield SimpleNamespace(name="feature", args=(feature,))
        else:
            for marker in self.added_markers:
                if marker.name == name:
                    yield marker

    def add_marker(self, marker):
        self.added_markers.append(marker)


def _skip_reasons(item: DummyItem):
    return [m.kwargs.get("reason") for m in item.added_markers if m.name == "skip"]


def test_node_role_only_skips_unmarked_tests(monkeypatch):
    monkeypatch.setenv("NODE_ROLE_ONLY", "1")
    marked = DummyItem(["Terminal"])
    unmarked = DummyItem()

    conftest.pytest_collection_modifyitems(SimpleNamespace(), [marked, unmarked])

    assert _skip_reasons(marked) == []
    assert _skip_reasons(unmarked) == [
        "missing role marker while NODE_ROLE_ONLY is enabled"
    ]


def test_node_role_filter_combines_with_role_only(monkeypatch):
    monkeypatch.setenv("NODE_ROLE_ONLY", "true")
    monkeypatch.setenv("NODE_ROLE", "Control")

    control_only = DummyItem(["Control"])
    terminal_only = DummyItem(["Terminal"])
    shared = DummyItem(["Control", "Terminal"])
    unmarked = DummyItem()

    items = [control_only, terminal_only, shared, unmarked]
    conftest.pytest_collection_modifyitems(SimpleNamespace(), items)

    assert _skip_reasons(control_only) == []
    assert _skip_reasons(terminal_only) == ["not run for Control role"]
    assert _skip_reasons(shared) == []
    assert _skip_reasons(unmarked) == [
        "missing role marker while NODE_ROLE_ONLY is enabled"
    ]


def test_node_feature_filter_skips_missing_features(monkeypatch):
    monkeypatch.setenv("NODE_FEATURES", "lcd-screen")
    monkeypatch.setenv("NODE_ROLE", "Terminal")

    lcd = DummyItem(features=["lcd-screen"])
    rfid = DummyItem(features=["rfid-scanner"])

    conftest.pytest_collection_modifyitems(SimpleNamespace(), [lcd, rfid])

    assert _skip_reasons(lcd) == []
    assert _skip_reasons(rfid) == [
        "rfid-scanner feature(s) not enabled for Terminal role"
    ]


def test_node_feature_filter_allows_unset_env(monkeypatch):
    monkeypatch.delenv("NODE_FEATURES", raising=False)

    item = DummyItem(features=["lcd-screen"])
    conftest.pytest_collection_modifyitems(SimpleNamespace(), [item])

    assert _skip_reasons(item) == []
