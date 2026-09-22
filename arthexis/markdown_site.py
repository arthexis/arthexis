"""README-rooted Markdown site for trusted operator documentation."""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import markdown
from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render

README_NAME = "README.md"
MARKDOWN_EXTENSIONS = ("extra", "toc")
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def _site_root() -> Path:
    return Path(settings.BASE_DIR).resolve()


def _readme(root: Path | None = None) -> Path:
    return (root or _site_root()) / README_NAME


def _render_source(source: str) -> str:
    return markdown.markdown(source, extensions=list(MARKDOWN_EXTENSIONS))


def _render_source_with_toc(source: str) -> tuple[str, str]:
    renderer = markdown.Markdown(extensions=list(MARKDOWN_EXTENSIONS))
    return renderer.convert(source), renderer.toc


def _markdown_links(document: Path, root: Path) -> set[Path]:
    parser = _LinkCollector()
    parser.feed(_render_source(document.read_text(encoding="utf-8")))
    linked: set[Path] = set()
    for href in parser.hrefs:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
            continue
        path = unquote(parsed.path)
        if not path.lower().endswith(".md"):
            continue
        candidate = (document.parent / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            linked.add(candidate)
    return linked


def public_markdown_files(root: Path | None = None) -> frozenset[Path]:
    """Return Markdown files transitively reachable from the root README."""

    root = (root or _site_root()).resolve()
    readme = _readme(root)
    if not readme.is_file():
        return frozenset()

    public: set[Path] = set()
    pending = [readme]
    while pending:
        document = pending.pop()
        if document in public:
            continue
        public.add(document)
        pending.extend(_markdown_links(document, root) - public)
    return frozenset(public)


def _resolve_public_document(document: str, root: Path | None = None) -> Path:
    root = (root or _site_root()).resolve()
    candidate = (root / document).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise Http404("Markdown document not found") from exc
    if candidate.suffix.lower() != ".md" or candidate not in public_markdown_files(root):
        raise Http404("Markdown document not found")
    return candidate


def _title_for(source: str, document: Path) -> str:
    match = _HEADING_RE.search(source)
    return match.group(1).strip() if match else document.stem.replace("-", " ").title()


def _render_document(request: HttpRequest, document: Path) -> HttpResponse:
    source = document.read_text(encoding="utf-8")
    content, toc = _render_source_with_toc(source)
    return render(
        request,
        "base/markdown_site.html",
        {
            "content": content,
            "title": _title_for(source, document),
            "toc": toc,
        },
    )


def markdown_home(request: HttpRequest) -> HttpResponse:
    """Render the repository README as the public site root."""

    readme = _readme()
    if not readme.is_file():
        raise Http404("README not found")
    return _render_document(request, readme)


def markdown_document(request: HttpRequest, document: str) -> HttpResponse:
    """Render one README-reachable Markdown document."""

    target = _resolve_public_document(document)
    if target == _readme().resolve():
        return redirect("markdown-home")
    return _render_document(request, target)
