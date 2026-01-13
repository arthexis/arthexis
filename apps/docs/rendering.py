import csv
import io
import re
from html import escape
from pathlib import Path

import bleach
import markdown

from apps.docs import assets


MARKDOWN_EXTENSIONS = ["toc", "tables", "mdx_truly_sane_lists"]

_ALLOWED_MARKDOWN_TAGS = set(bleach.sanitizer.ALLOWED_TAGS) | {
    "blockquote",
    "code",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "p",
    "pre",
    "span",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
}
_ALLOWED_MARKDOWN_ATTRIBUTES = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "rel"],
    "code": ["class"],
    "div": ["class"],
    "h1": ["id", "class"],
    "h2": ["id", "class"],
    "h3": ["id", "class"],
    "h4": ["id", "class"],
    "h5": ["id", "class"],
    "h6": ["id", "class"],
    "img": ["src", "alt", "title", "loading"],
    "p": ["class"],
    "pre": ["class"],
    "span": ["class"],
    "table": ["class"],
    "tbody": ["class"],
    "td": ["class", "colspan", "rowspan"],
    "tfoot": ["class"],
    "th": ["class", "colspan", "rowspan", "scope"],
    "thead": ["class"],
    "tr": ["class"],
}
_ALLOWED_MARKDOWN_PROTOCOLS = set(bleach.sanitizer.ALLOWED_PROTOCOLS)


def _sanitize_html(html: str) -> str:
    return bleach.clean(
        html,
        tags=_ALLOWED_MARKDOWN_TAGS,
        attributes=_ALLOWED_MARKDOWN_ATTRIBUTES,
        protocols=_ALLOWED_MARKDOWN_PROTOCOLS,
        strip=True,
    )

MARKDOWN_FILE_EXTENSIONS = {".md", ".markdown"}
PLAINTEXT_FILE_EXTENSIONS = {".txt", ".text"}
CSV_FILE_EXTENSIONS = {".csv"}


def render_markdown_with_toc(text: str) -> tuple[str, str]:
    """Render ``text`` to HTML and return the HTML and stripped TOC."""

    md = markdown.Markdown(extensions=MARKDOWN_EXTENSIONS)
    html = md.convert(text)
    html = assets.rewrite_markdown_asset_links(html)
    html = assets.strip_http_subresources(html)
    html = _sanitize_html(html)
    toc_html = md.toc
    toc_html = strip_toc_wrapper(toc_html)
    toc_html = _sanitize_html(toc_html)
    return html, toc_html


def render_plain_text_document(text: str) -> tuple[str, str]:
    """Render plain text content using a preformatted block."""

    html = (
        '<pre class="reader-plain-text bg-body-tertiary border rounded p-3 text-break">'
        f"{escape(text)}"
        "</pre>"
    )
    return html, ""


def render_csv_document(text: str) -> tuple[str, str]:
    """Render CSV content into a responsive HTML table."""

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        empty_html = (
            '<div class="table-responsive">'
            '<table class="table table-striped table-bordered table-sm reader-table">'
            "<tbody><tr><td class=\"text-muted\">No data available.</td></tr></tbody>"
            "</table></div>"
        )
        return empty_html, ""

    column_count = max(len(row) for row in rows)

    def _normalize(row: list[str]) -> list[str]:
        normalized = list(row)
        if len(normalized) < column_count:
            normalized.extend([""] * (column_count - len(normalized)))
        return normalized

    header_cells = "".join(
        f"<th scope=\"col\">{escape(value)}</th>" for value in _normalize(rows[0])
    )
    header_html = f"<thead><tr>{header_cells}</tr></thead>"

    body_rows = rows[1:]
    if body_rows:
        body_html = "".join(
            "<tr>"
            + "".join(f"<td>{escape(value)}</td>" for value in _normalize(row))
            + "</tr>"
            for row in body_rows
        )
    else:
        body_html = (
            f"<tr><td class=\"text-muted\" colspan=\"{column_count}\">No rows available.</td></tr>"
        )
    body_html = f"<tbody>{body_html}</tbody>"

    table_html = (
        '<div class="table-responsive">'
        '<table class="table table-striped table-bordered table-sm reader-table">'
        f"{header_html}{body_html}</table></div>"
    )
    return table_html, ""


def render_code_document(text: str) -> tuple[str, str]:
    """Render arbitrary text content inside a code viewer block."""

    html = (
        '<pre class="reader-code-viewer bg-body-tertiary border rounded p-3">'
        f"<code class=\"font-monospace\">{escape(text)}</code>"
        "</pre>"
    )
    return html, ""


def read_document_text(file_path: Path) -> str:
    """Read ``file_path`` as UTF-8 text, replacing undecodable bytes."""

    return file_path.read_text(encoding="utf-8", errors="replace")


def render_document_file(file_path: Path) -> tuple[str, str]:
    """Render a documentation file according to its extension."""

    extension = file_path.suffix.lower()
    text = read_document_text(file_path)
    if extension in MARKDOWN_FILE_EXTENSIONS:
        return render_markdown_with_toc(text)
    if extension in CSV_FILE_EXTENSIONS:
        return render_csv_document(text)
    if extension in PLAINTEXT_FILE_EXTENSIONS:
        return render_plain_text_document(text)
    return render_code_document(text)


def strip_toc_wrapper(toc_html: str) -> str:
    """Normalize ``markdown``'s TOC output by removing the wrapper ``div``."""

    toc_html = toc_html.strip()
    if toc_html.startswith('<div class="toc">'):
        toc_html = toc_html[len('<div class="toc">') :]
        if toc_html.endswith("</div>"):
            toc_html = toc_html[: -len("</div>")]
    return toc_html.strip()


def split_html_sections(html: str, keep_sections: int) -> tuple[str, str]:
    """Return ``keep_sections`` leading sections and the remaining HTML."""

    if keep_sections < 1:
        return "", html

    heading_matches = list(re.finditer(r"<h[1-6]\b[^>]*>", html, flags=re.IGNORECASE))
    if len(heading_matches) <= keep_sections:
        return html, ""

    split_index = heading_matches[keep_sections].start()
    return html[:split_index], html[split_index:]
