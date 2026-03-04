"""Standalone pytest collector script used by suite discovery."""

from __future__ import annotations

import json

import pytest


class CollectorPlugin:
    """Capture discovered pytest item metadata during collection."""

    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def pytest_collection_modifyitems(self, session, config, items) -> None:  # noqa: ARG002
        """Store collection metadata for each discovered pytest item."""

        for item in items:
            path = str(getattr(item, "path", "") or "")
            self.items.append(
                {
                    "node_id": item.nodeid,
                    "name": item.name,
                    "file_path": path,
                    "module_path": getattr(getattr(item, "module", None), "__name__", "") or "",
                    "class_name": getattr(getattr(item, "cls", None), "__name__", "") or "",
                    "marks": sorted(
                        [
                            keyword
                            for keyword, value in item.keywords.items()
                            if value and isinstance(keyword, str)
                        ]
                    ),
                }
            )


def main() -> int:
    """Run pytest in collect-only mode and emit JSON payload."""

    plugin = CollectorPlugin()
    return_code = pytest.main(["--collect-only", "-q", "--disable-warnings"], plugins=[plugin])
    print(json.dumps({"returncode": return_code, "items": plugin.items}))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
