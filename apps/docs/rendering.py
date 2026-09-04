"""Compatibility rendering helpers after removal of the docs Django app."""

from html import escape
from pathlib import Path

from apps.ocpp.markdown import render_markdown_with_toc

MARKDOWN_FILE_EXTENSIONS = {".md", ".markdown"}
PLAINTEXT_FILE_EXTENSIONS = {".txt", ".text"}


def render_plain_text_document(text: str) -> tuple[str, str]:
    return (
        '<pre class="reader-plain-text bg-body-tertiary border rounded p-3 text-break">'
        f"{escape(text)}"
        "</pre>",
        "",
    )


def render_code_document(text: str) -> tuple[str, str]:
    return (
        '<pre class="reader-code-viewer bg-body-tertiary border rounded p-3">'
        f'<code class="font-monospace">{escape(text)}</code>'
        "</pre>",
        "",
    )


def render_document_file(file_path: Path) -> tuple[str, str]:
    """Render a retained repository text document without restoring docs routes."""

    text = file_path.read_text(encoding="utf-8", errors="replace")
    extension = file_path.suffix.lower()
    if extension in MARKDOWN_FILE_EXTENSIONS:
        return render_markdown_with_toc(text)
    if extension in PLAINTEXT_FILE_EXTENSIONS:
        return render_plain_text_document(text)
    return render_code_document(text)
