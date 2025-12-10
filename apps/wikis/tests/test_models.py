from __future__ import annotations

from apps.wikis.models import WikiSummary


def test_wiki_summary_first_paragraph_prefers_first_block():
    summary = WikiSummary(
        title="Example",
        extract="First paragraph.\n\nSecond paragraph follows.",
        url=None,
        language="en",
    )

    assert summary.first_paragraph == "First paragraph."


def test_wiki_summary_first_paragraph_ignores_leading_blank_lines():
    summary = WikiSummary(
        title="Example",
        extract="\n\n\nFirst paragraph after blanks.\n\nSecond paragraph.",
        url=None,
        language="en",
    )

    assert summary.first_paragraph == "First paragraph after blanks."


def test_wiki_summary_first_paragraph_strips_html_tags():
    summary = WikiSummary(
        title="Example",
        extract='<p>Here is a <a href="https://example.com">link</a> and text.</p><p>More text.</p>',
        url=None,
        language="en",
    )

    assert summary.first_paragraph == "Here is a link and text."


def test_wiki_summary_first_paragraph_html_preserves_links_and_sanitizes():
    summary = WikiSummary(
        title="Example",
        extract='<p>Intro with <a href="https://example.com" onclick="evil()">safe link</a> and <script>alert(1)</script>.</p>',
        url=None,
        language="en",
    )

    html = summary.first_paragraph_html

    assert "onclick" not in html
    assert "script" not in html
    assert '<a href="https://example.com"' in html
