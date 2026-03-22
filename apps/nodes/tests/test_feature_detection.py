from pathlib import Path

from apps.nodes.feature_detection import _invoke_detector


class _NodeStub:
    pass


def test_invoke_detector_falls_back_to_node_and_base_path() -> None:
    """Compatibility invocation should support callbacks without base_dir."""

    captured: dict[str, object] = {}

    def callback(slug: str, *, node, base_path: Path):
        captured["slug"] = slug
        captured["node"] = node
        captured["base_path"] = base_path
        return True

    node = _NodeStub()
    base_dir = Path("/tmp/base-dir")
    base_path = Path("/tmp/base-path")

    result = _invoke_detector(
        callback,
        "rfid-scanner",
        node=node,
        base_dir=base_dir,
        base_path=base_path,
    )

    assert result is True
    assert captured == {
        "slug": "rfid-scanner",
        "node": node,
        "base_path": base_path,
    }
