from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.docs import rendering, views

pytestmark = pytest.mark.django_db


def test_readme_versioning_policy_link_uses_local_docs_route(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
) -> None:
    monkeypatch.setattr(views.Node, "get_local", lambda: SimpleNamespace(role=None))
    request = rf.get("/read/")
    request.user = AnonymousUser()

    response = views.render_readme_page(request)

    assert response.status_code == 200
    content = response.content.decode()
    portable_readme_link = (
        "github.com/arthexis/arthexis/blob/main/docs/development/"
        "versioning-maturity-policy.md"
    )
    assert f'href="https://{portable_readme_link}"' in content


def test_markdown_renderer_keeps_github_doc_links_by_default() -> None:
    github_url = (
        "https://github.com/arthexis/arthexis/blob/main/docs/development/"
        "versioning-maturity-policy.md"
    )

    content, _toc = rendering.render_markdown_with_toc(
        f"[Versioning and Maturity Policy]({github_url})"
    )

    assert f'href="{github_url}"' in content
    assert 'href="/docs/development/versioning-maturity-policy.md"' not in content


def test_readme_module_path_cannot_escape_suite_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A malformed module path must not select a README outside the suite."""

    root = tmp_path / "suite"
    root.mkdir()
    root_readme = root / "README.md"
    root_readme.write_text("# Root\n", encoding="utf-8")
    escaped = tmp_path / "README.md"
    escaped.write_text("# Escaped\n", encoding="utf-8")

    class Modules:
        def filter(self, **kwargs):
            return self

        def select_related(self, *args):
            return self

        def prefetch_related(self, *args):
            return [
                SimpleNamespace(
                    path="..",
                    meets_feature_requirements=lambda enabled: True,
                )
            ]

    monkeypatch.setattr(views.Module.objects, "for_role", lambda role: Modules())
    monkeypatch.setattr(views.settings, "BASE_DIR", root)

    document = views._locate_readme_document(role=None, lang="")

    assert document.file == root_readme
