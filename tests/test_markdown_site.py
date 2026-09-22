from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import Client, SimpleTestCase, override_settings

from arthexis.markdown_site import public_markdown_files


class MarkdownSiteTests(SimpleTestCase):
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_repository_readme_is_public_home(self) -> None:
        response = Client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, '<h1 id="constellation">Constellation</h1>', html=True
        )
        self.assertContains(
            response,
            '<h2 id="operational-capabilities">Operational Capabilities</h2>',
            html=True,
        )
        self.assertNotContains(response, "/admin/")

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_linked_operator_guide_is_public(self) -> None:
        response = Client().get("/docs/operator-guide.md")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1 id="operator-guide">Operator Guide</h1>', html=True)

    def test_only_readme_reachable_markdown_is_exposed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text(
                "# Home\n\n[Guide](docs/guide.md)\n", encoding="utf-8"
            )
            docs = root / "docs"
            docs.mkdir()
            (docs / "guide.md").write_text(
                "# Guide\n\n[Deep](deep.md)\n", encoding="utf-8"
            )
            (docs / "deep.md").write_text("# Deep\n", encoding="utf-8")
            (root / "secret.md").write_text("# Secret\n", encoding="utf-8")

            public = public_markdown_files(root)

            self.assertIn(root / "README.md", public)
            self.assertIn(docs / "guide.md", public)
            self.assertIn(docs / "deep.md", public)
            self.assertNotIn(root / "secret.md", public)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_unlinked_repository_markdown_returns_404(self) -> None:
        response = Client().get("/FROZEN_1X_SOURCE.md")

        self.assertEqual(response.status_code, 404)


class MarkdownPresentationTests(SimpleTestCase):
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_home_renders_contents_sidebar(self) -> None:
        response = Client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Contents"')
        self.assertContains(response, 'href="#purpose"')
        self.assertContains(response, 'href="#suite-features"')

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_markdown_site_is_dark_first(self) -> None:
        response = Client().get("/")

        self.assertContains(response, "color-scheme: dark")
        self.assertContains(response, 'class="site-header"')
        self.assertContains(response, 'class="markdown-body"')
