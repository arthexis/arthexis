from pathlib import Path

from apps.docs import views


def test_collect_document_library_lists_top_level_entries_only(tmp_path, monkeypatch):
    docs_root = tmp_path / "docs"
    apps_docs_root = tmp_path / "apps" / "docs"
    docs_root.mkdir(parents=True, exist_ok=True)
    (docs_root / "README.md").write_text("# Home\n\nTop level doc", encoding="utf-8")
    (docs_root / "guides" / "install.md").parent.mkdir(parents=True, exist_ok=True)
    (docs_root / "guides" / "install.md").write_text("# Install\n\nInstall flow", encoding="utf-8")
    (apps_docs_root / "cookbooks" / "alpha.md").parent.mkdir(parents=True, exist_ok=True)
    (apps_docs_root / "cookbooks" / "alpha.md").write_text("# Alpha\n\nApp docs", encoding="utf-8")

    def fake_reverse(route, args=None):
        if route == "docs:docs-library":
            return "/docs/library/"
        return f"/{route}/{(args or [''])[0]}"

    monkeypatch.setattr(views, "reverse", fake_reverse)

    sections = views._collect_document_library(tmp_path)
    docs_items = sections[0]["items"]

    assert [item["label"] for item in docs_items] == ["guides/", "README.md"]
    assert docs_items[0]["kind"] == "folder"
    assert docs_items[0]["url"] == "/docs/library/?docs_path=guides"


def test_collect_document_library_allows_folder_drilldown(tmp_path, monkeypatch):
    docs_root = tmp_path / "docs"
    (docs_root / "guides" / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (docs_root / "guides" / "README.md").write_text("# Guides\n\nOverview", encoding="utf-8")
    (docs_root / "guides" / "install.md").write_text("# Install\n\nSteps", encoding="utf-8")
    (docs_root / "guides" / "advanced" / "tips.md").parent.mkdir(parents=True, exist_ok=True)
    (docs_root / "guides" / "advanced" / "tips.md").write_text("# Tips\n\nNested", encoding="utf-8")

    def fake_reverse(route, args=None):
        if route == "docs:docs-library":
            return "/docs/library/"
        return f"/{route}/{(args or [''])[0]}"

    monkeypatch.setattr(views, "reverse", fake_reverse)

    sections = views._collect_document_library(tmp_path, docs_prefix="guides")
    docs_section = sections[0]
    items = docs_section["items"]

    assert docs_section["current_prefix"] == "guides"
    assert docs_section["parent_url"] == "/docs/library/"
    assert [item["label"] for item in items] == ["advanced/", "README.md", "install.md"]
