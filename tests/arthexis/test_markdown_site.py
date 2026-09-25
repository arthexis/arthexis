from pathlib import Path

from django.test import Client, override_settings

from arthexis.markdown_site import public_markdown_files


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_repository_readme_is_public_home() -> None:
    response = Client().get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert '<h1 id="constellation">Constellation</h1>' in content
    assert (
        '<h2 id="operational-capabilities">Operational Capabilities</h2>'
        in content
    )
    assert '<a href="/admin/"' not in content


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_linked_operator_guide_is_public() -> None:
    response = Client().get("/docs/operator-guide.md")

    assert response.status_code == 200
    assert '<h1 id="operator-guide">Operator Guide</h1>' in response.content.decode()


def test_only_readme_reachable_markdown_is_exposed(tmp_path: Path) -> None:
    root = tmp_path
    (root / "README.md").write_text(
        "# Home\n\n[Guide](docs/guide.md)\n",
        encoding="utf-8",
    )
    docs = root / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# Guide\n\n[Deep](deep.md)\n",
        encoding="utf-8",
    )
    (docs / "deep.md").write_text("# Deep\n", encoding="utf-8")
    (root / "secret.md").write_text("# Secret\n", encoding="utf-8")

    public = public_markdown_files(root)

    assert root / "README.md" in public
    assert docs / "guide.md" in public
    assert docs / "deep.md" in public
    assert root / "secret.md" not in public


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_unlinked_repository_markdown_returns_404() -> None:
    response = Client().get("/FROZEN_1X_SOURCE.md")

    assert response.status_code == 404


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_home_renders_contents_sidebar() -> None:
    response = Client().get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert 'aria-label="Contents"' in content
    assert 'href="#purpose"' in content
    assert 'href="#suite-features"' in content


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_markdown_site_is_dark_first() -> None:
    content = Client().get("/").content.decode()

    assert "color-scheme: dark" in content
    assert 'class="site-header"' in content
    assert 'class="markdown-body"' in content
