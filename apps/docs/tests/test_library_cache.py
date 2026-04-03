"""Tests for document library caching behavior."""

from pathlib import Path

from django.core.cache import cache

from apps.docs import views


def test_document_library_cache_reuses_path_scan_for_unique_prefixes(monkeypatch):
    """Unique query prefixes should not trigger repeated filesystem scans."""

    cache.clear()
    root_base = Path("/tmp/arthexis-test-root")
    docs_root = root_base / "docs"
    apps_docs_root = root_base / "apps" / "docs"
    docs_file = docs_root / "guide" / "intro.md"
    apps_docs_file = apps_docs_root / "billing" / "overview.md"

    calls: list[Path] = []

    def _fake_iter_document_paths(root: Path) -> list[Path]:
        calls.append(root)
        if root == docs_root:
            return [docs_file]
        if root == apps_docs_root:
            return [apps_docs_file]
        return []

    monkeypatch.setattr(views, "_iter_document_paths", _fake_iter_document_paths)

    first = views._get_cached_document_library(root_base, docs_prefix="guide")
    second = views._get_cached_document_library(root_base, docs_prefix="another-random-prefix")

    assert len(calls) == 2
    assert first[0]["items"]
    assert second[0]["items"] == []

def test_document_library_cache_scopes_paths_to_root_base(monkeypatch):
    """Cached paths should be isolated per root base."""

    cache.clear()
    first_root_base = Path("/tmp/arthexis-release-a")
    second_root_base = Path("/tmp/arthexis-release-b")
    first_docs_root = first_root_base / "docs"
    first_apps_docs_root = first_root_base / "apps" / "docs"
    second_docs_root = second_root_base / "docs"
    second_apps_docs_root = second_root_base / "apps" / "docs"
    first_docs_file = first_docs_root / "guide" / "intro.md"
    second_docs_file = second_docs_root / "guide" / "intro.md"

    calls: list[Path] = []

    def _fake_iter_document_paths(root: Path) -> list[Path]:
        calls.append(root)
        if root == first_docs_root:
            return [first_docs_file]
        if root == first_apps_docs_root:
            return []
        if root == second_docs_root:
            return [second_docs_file]
        if root == second_apps_docs_root:
            return []
        return []

    monkeypatch.setattr(views, "_iter_document_paths", _fake_iter_document_paths)

    first_sections = views._get_cached_document_library(first_root_base, docs_prefix="guide")
    second_sections = views._get_cached_document_library(second_root_base, docs_prefix="guide")

    assert len(calls) == 4
    assert first_sections[0]["items"]
    assert second_sections[0]["items"]

