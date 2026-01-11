from __future__ import annotations

import pytest

from apps.docs import rendering


@pytest.mark.integration
def test_render_csv_document_pads_rows_and_columns():
    html, toc = rendering.render_csv_document("header1,header2\nvalue1\nvalue2,extra,columns")

    assert toc == ""
    assert "<th scope=\"col\">header1</th><th scope=\"col\">header2</th><th scope=\"col\"></th>" in html
    assert "<td>value1</td><td></td><td></td>" in html
    assert "<td>value2</td><td>extra</td><td>columns</td>" in html


def test_render_plain_text_document_escapes_html():
    html, toc = rendering.render_plain_text_document("<script>alert('xss')</script>")

    assert toc == ""
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html


@pytest.mark.integration
def test_render_document_file_selects_renderer(tmp_path):
    markdown_file = tmp_path / "README.md"
    markdown_file.write_text("# Title", encoding="utf-8")

    csv_file = tmp_path / "table.csv"
    csv_file.write_text("a,b\n1,2", encoding="utf-8")

    text_file = tmp_path / "note.txt"
    text_file.write_text("plain text", encoding="utf-8")

    code_file = tmp_path / "script.py"
    code_file.write_text("print('hi')", encoding="utf-8")

    html, _ = rendering.render_document_file(markdown_file)
    assert "<h1" in html

    html, _ = rendering.render_document_file(csv_file)
    assert "reader-table" in html

    html, _ = rendering.render_document_file(text_file)
    assert "reader-plain-text" in html

    html, _ = rendering.render_document_file(code_file)
    assert "reader-code-viewer" in html


@pytest.mark.integration
def test_split_html_sections_splits_after_heading():
    html = "<h1>Title</h1><p>Intro</p><h2>Section</h2><p>Body</p>"

    initial, remaining = rendering.split_html_sections(html, 1)

    assert initial.startswith("<h1>Title</h1>")
    assert remaining.startswith("<h2>Section</h2>")
