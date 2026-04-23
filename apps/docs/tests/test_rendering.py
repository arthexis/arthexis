from pathlib import Path

import pytest

from apps.docs import rendering
from apps.sigils.models import SigilRoot


@pytest.mark.django_db
def test_read_document_text_only_resolves_user_safe_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_RENDERING_SECRET", "supersecret")
    SigilRoot.objects.update_or_create(
        prefix="ENV",
        defaults={
            "context_type": SigilRoot.Context.CONFIG,
            "is_user_safe": False,
        },
    )
    file_path = Path(tmp_path) / "sample.md"
    file_path.write_text("Token: [ENV.DOCS_RENDERING_SECRET]", encoding="utf-8")

    rendered = rendering.read_document_text(file_path)

    assert rendered == "Token: [ENV.DOCS_RENDERING_SECRET]"


@pytest.mark.django_db
def test_read_document_text_resolves_user_safe_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_RENDERING_SAFE_VALUE", "visible")
    SigilRoot.objects.update_or_create(
        prefix="ENV",
        defaults={
            "context_type": SigilRoot.Context.CONFIG,
            "is_user_safe": True,
        },
    )
    file_path = Path(tmp_path) / "safe.md"
    file_path.write_text("Token: [ENV.DOCS_RENDERING_SAFE_VALUE]", encoding="utf-8")

    rendered = rendering.read_document_text(file_path)

    assert rendered == "Token: visible"
