"""Markdown rendering helpers for OCPP-facing content."""

import re

import bleach
import markdown

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

_MARKDOWN_ASSET_TAG_PATTERN = re.compile(
    r"<(?P<tag>img|script|link|audio|video|source|iframe|embed)\b[^>]*>",
    re.IGNORECASE,
)
_MARKDOWN_HTTP_ASSET_ATTRIBUTE_PATTERN = re.compile(
    r"\s+(?P<attr>src|href|srcset)=(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)


def _sanitize_html(html: str) -> str:
    return bleach.clean(
        html,
        tags=_ALLOWED_MARKDOWN_TAGS,
        attributes=_ALLOWED_MARKDOWN_ATTRIBUTES,
        protocols=_ALLOWED_MARKDOWN_PROTOCOLS,
        strip=True,
    )


def _strip_http_subresources(html: str) -> str:
    """Strip insecure HTTP subresource URLs from rendered Markdown."""

    def _strip_http_attributes(match: re.Match[str]) -> str:
        tag_html = match.group(0)

        def _remove_attr(attr_match: re.Match[str]) -> str:
            if "http://" in attr_match.group("value").lower():
                return ""
            return attr_match.group(0)

        return _MARKDOWN_HTTP_ASSET_ATTRIBUTE_PATTERN.sub(_remove_attr, tag_html)

    return _MARKDOWN_ASSET_TAG_PATTERN.sub(_strip_http_attributes, html)


def _rewrite_mermaid_blocks(html: str) -> str:
    """Replace fenced Mermaid code blocks with Mermaid container divs."""

    def _replace(match: re.Match[str]) -> str:
        diagram = match.group("diagram").strip("\n")
        return f'<div class="mermaid">{diagram}</div>'

    return re.sub(
        r'<pre><code class="language-mermaid">(?P<diagram>.*?)</code></pre>',
        _replace,
        html,
        flags=re.DOTALL,
    )


def _strip_toc_wrapper(toc_html: str) -> str:
    toc_html = toc_html.strip()
    if toc_html.startswith('<div class="toc">'):
        toc_html = toc_html[len('<div class="toc">') :]
        if toc_html.endswith("</div>"):
            toc_html = toc_html[: -len("</div>")]
    return toc_html.strip()


def render_markdown_with_toc(text: str) -> tuple[str, str]:
    """Render Markdown to sanitized HTML and return HTML plus stripped TOC."""

    md = markdown.Markdown(extensions=MARKDOWN_EXTENSIONS)
    html = md.convert(text)
    html = _strip_http_subresources(html)
    html = _rewrite_mermaid_blocks(html)
    html = _sanitize_html(html)
    toc_html = _sanitize_html(_strip_toc_wrapper(md.toc))
    return html, toc_html
