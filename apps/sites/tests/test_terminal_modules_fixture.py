import json
from pathlib import Path


def test_terminal_modules_fixture_includes_docs_module_pill():
    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "default__modules_terminal.json"
    )
    entries = json.loads(fixture_path.read_text(encoding="utf-8"))

    docs_module = next(
        (
            entry
            for entry in entries
            if entry.get("model") == "modules.module"
            and entry.get("fields", {}).get("path") == "/docs/"
        ),
        None,
    )

    assert docs_module is not None
    assert docs_module["fields"]["application"] == ["docs"]
    assert docs_module["fields"]["roles"] == [["Terminal"]]

    docs_landing = next(
        (
            entry
            for entry in entries
            if entry.get("model") == "pages.landing"
            and entry.get("fields", {}).get("module") == ["/docs/"]
        ),
        None,
    )
    assert docs_landing is not None
    assert docs_landing["fields"]["path"] == "/docs/library/"
