"""Shared rendering and sanitization helpers for publishing apps."""

from __future__ import annotations

import bleach

MARKDOWN_EXTENSIONS = ["toc", "tables", "mdx_truly_sane_lists", "fenced_code"]

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

_WIKI_INLINE_ALLOWED_TAGS = ["a", "b", "strong", "i", "em", "u", "span", "sup", "sub", "code", "br"]
_WIKI_INLINE_ALLOWED_ATTRIBUTES = {"a": ["href", "title", "rel"]}
_WIKI_INLINE_ALLOWED_PROTOCOLS = ["http", "https"]


def sanitize_markdown_html(html: str) -> str:
    """Return sanitized HTML for markdown-rendered content."""

    return bleach.clean(
        html,
        tags=_ALLOWED_MARKDOWN_TAGS,
        attributes=_ALLOWED_MARKDOWN_ATTRIBUTES,
        protocols=_ALLOWED_MARKDOWN_PROTOCOLS,
        strip=True,
    )


def sanitize_wiki_inline_html(html: str) -> str:
    """Return sanitized and linkified inline wiki HTML."""

    cleaned = bleach.clean(
        html,
        tags=_WIKI_INLINE_ALLOWED_TAGS,
        attributes=_WIKI_INLINE_ALLOWED_ATTRIBUTES,
        protocols=_WIKI_INLINE_ALLOWED_PROTOCOLS,
        strip=True,
    )
    return bleach.linkify(cleaned)


def strip_all_html_tags(html: str) -> str:
    """Return plain text with all HTML tags removed."""

    return bleach.clean(html, tags=[], strip=True)


__all__ = [
    "MARKDOWN_EXTENSIONS",
    "sanitize_markdown_html",
    "sanitize_wiki_inline_html",
    "strip_all_html_tags",
]
