from __future__ import annotations

import pytest

from apps.docs import rendering

pytestmark = pytest.mark.critical


def test_render_plain_text_document_escapes_html():
    html, toc = rendering.render_plain_text_document("<script>alert('xss')</script>")

    assert toc == ""
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html


def test_render_markdown_with_mermaid_blocks():
    """Ensure Mermaid fenced code blocks render as Mermaid containers."""

    html, toc = rendering.render_markdown_with_toc(
        "```mermaid\nflowchart TD\n  A --> B\n```"
    )

    assert toc in {"", "<ul></ul>"}
    assert "<div class=\"mermaid\">" in html
    assert "flowchart TD" in html
    assert "A --> B" in html or "A --&gt; B" in html
