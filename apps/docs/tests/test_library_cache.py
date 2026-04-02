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
