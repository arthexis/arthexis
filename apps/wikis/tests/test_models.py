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
