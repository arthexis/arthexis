from __future__ import annotations

import json
from pathlib import Path


def _fixture_files() -> list[Path]:
    project_root = Path(__file__).resolve().parents[3]
    return sorted(project_root.glob("**/fixtures/*.json"))


def test_fixtures_use_natural_keys():
    project_root = Path(__file__).resolve().parents[3]
    offenders: dict[str, list[int]] = {}

    for fixture in _fixture_files():
        try:
            content = json.loads(fixture.read_text())
        except json.JSONDecodeError:
            continue

        if not isinstance(content, list):
            continue

        pk_entries = [
            index + 1
            for index, entry in enumerate(content)
            if isinstance(entry, dict) and "pk" in entry
        ]

        if pk_entries:
            offenders[str(fixture.relative_to(project_root))] = pk_entries

    assert not offenders, (
        "Fixtures should use natural keys instead of synthetic primary keys: "
        + ", ".join(
            f"{path} (objects {', '.join(map(str, rows))})"
            for path, rows in sorted(offenders.items())
        )
    )

