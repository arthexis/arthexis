import csv
import io
import logging
import mimetypes
import re
from html import escape
from pathlib import Path
from types import SimpleNamespace

import markdown
from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils._os import safe_join
from django.views.decorators.cache import never_cache

from apps.nodes.models import Node
from apps.pages.models import Module


logger = logging.getLogger(__name__)


MARKDOWN_EXTENSIONS = ["toc", "tables", "mdx_truly_sane_lists"]

MARKDOWN_FILE_EXTENSIONS = {".md", ".markdown"}
PLAINTEXT_FILE_EXTENSIONS = {".txt", ".text"}
CSV_FILE_EXTENSIONS = {".csv"}

MARKDOWN_IMAGE_PATTERN = re.compile(
    r"(?P<prefix><img\b[^>]*\bsrc=[\"\'])(?P<scheme>(?:static|work))://(?P<path>[^\"\']+)(?P<suffix>[\"\'])",
    re.IGNORECASE,
)

ALLOWED_IMAGE_EXTENSIONS = {
    ".apng",
    ".avif",
    ".gif",
    ".jpg",
    ".jpeg",
    ".png",
    ".svg",
    ".webp",
}


def render_markdown_with_toc(text: str) -> tuple[str, str]:
    """Render ``text`` to HTML and return the HTML and stripped TOC."""

    md = markdown.Markdown(extensions=MARKDOWN_EXTENSIONS)
    html = md.convert(text)
    html = _rewrite_markdown_asset_links(html)
    toc_html = md.toc
    toc_html = _strip_toc_wrapper(toc_html)
    return html, toc_html


def _render_plain_text_document(text: str) -> tuple[str, str]:
    """Render plain text content using a preformatted block."""

    html = (
        '<pre class="reader-plain-text bg-body-tertiary border rounded p-3 text-break">'
        f"{escape(text)}"
        "</pre>"
    )
    return html, ""


def _render_csv_document(text: str) -> tuple[str, str]:
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


def _render_code_document(text: str) -> tuple[str, str]:
    """Render arbitrary text content inside a code viewer block."""

    html = (
        '<pre class="reader-code-viewer bg-body-tertiary border rounded p-3">'
        f"<code class=\"font-monospace\">{escape(text)}</code>"
        "</pre>"
    )
    return html, ""


def _read_document_text(file_path: Path) -> str:
    """Read ``file_path`` as UTF-8 text, replacing undecodable bytes."""

    return file_path.read_text(encoding="utf-8", errors="replace")


def _render_document_file(file_path: Path) -> tuple[str, str]:
    """Render a documentation file according to its extension."""

    extension = file_path.suffix.lower()
    text = _read_document_text(file_path)
    if extension in MARKDOWN_FILE_EXTENSIONS:
        return render_markdown_with_toc(text)
    if extension in CSV_FILE_EXTENSIONS:
        return _render_csv_document(text)
    if extension in PLAINTEXT_FILE_EXTENSIONS:
        return _render_plain_text_document(text)
    return _render_code_document(text)


def _strip_toc_wrapper(toc_html: str) -> str:
    """Normalize ``markdown``'s TOC output by removing the wrapper ``div``."""

    toc_html = toc_html.strip()
    if toc_html.startswith('<div class="toc">'):
        toc_html = toc_html[len('<div class="toc">') :]
        if toc_html.endswith("</div>"):
            toc_html = toc_html[: -len("</div>")]
    return toc_html.strip()


def _rewrite_markdown_asset_links(html: str) -> str:
    """Rewrite asset links that reference local asset schemes."""

    def _replace(match: re.Match[str]) -> str:
        scheme = match.group("scheme").lower()
        asset_path = match.group("path").lstrip("/")
        if not asset_path:
            return match.group(0)
        extension = Path(asset_path).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            return match.group(0)
        try:
            asset_url = reverse(
                "docs:readme-asset",
                kwargs={"source": scheme, "asset": asset_path},
            )
        except NoReverseMatch:
            return match.group(0)
        return f"{match.group('prefix')}{escape(asset_url)}{match.group('suffix')}"

    return MARKDOWN_IMAGE_PATTERN.sub(_replace, html)


def _resolve_static_asset(path: str) -> Path:
    normalized = path.lstrip("/")
    if not normalized:
        raise Http404("Asset not found")
    resolved = finders.find(normalized)
    if not resolved:
        raise Http404("Asset not found")
    if isinstance(resolved, (list, tuple)):
        resolved = resolved[0]
    file_path = Path(resolved)
    if file_path.is_dir():
        raise Http404("Asset not found")
    return file_path


def _resolve_work_asset(user, path: str) -> Path:
    if not (user and getattr(user, "is_authenticated", False)):
        raise PermissionDenied
    normalized = path.lstrip("/")
    if not normalized:
        raise Http404("Asset not found")
    username = getattr(user, "get_username", None)
    if callable(username):
        username = username()
    else:
        username = getattr(user, "username", "")
    username_component = Path(str(username or user.pk)).name
    base_work = Path(settings.BASE_DIR) / "work"
    try:
        user_dir = Path(safe_join(str(base_work), username_component))
        asset_path = Path(safe_join(str(user_dir), normalized))
    except SuspiciousFileOperation as exc:
        logger.warning("Rejected suspicious work asset path: %s", normalized, exc_info=exc)
        raise Http404("Asset not found") from exc
    try:
        user_dir_resolved = user_dir.resolve(strict=True)
    except FileNotFoundError as exc:
        logger.warning(
            "Work directory missing for asset request: %s", user_dir, exc_info=exc
        )
        raise Http404("Asset not found") from exc
    try:
        asset_resolved = asset_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise Http404("Asset not found") from exc
    try:
        asset_resolved.relative_to(user_dir_resolved)
    except ValueError as exc:
        logger.warning(
            "Rejected work asset outside directory: %s", asset_resolved, exc_info=exc
        )
        raise Http404("Asset not found") from exc
    if asset_resolved.is_dir():
        raise Http404("Asset not found")
    return asset_resolved


def _locate_readme_document(role, doc: str | None, lang: str) -> SimpleNamespace:
    app = (
        Module.objects.for_role(role)
        .filter(is_default=True, is_deleted=False)
        .select_related("application")
        .first()
    )
    app_slug = app.path.strip("/") if app else ""
    root_base = Path(settings.BASE_DIR).resolve()
    readme_base = (root_base / app_slug).resolve() if app_slug else root_base
    candidates: list[Path] = []

    if doc:
        normalized = doc.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        if not normalized:
            raise Http404("Document not found")
        doc_path = Path(normalized)
        if doc_path.is_absolute() or any(part == ".." for part in doc_path.parts):
            raise Http404("Document not found")

        relative_candidates: list[Path] = []

        def add_candidate(path: Path) -> None:
            if path not in relative_candidates:
                relative_candidates.append(path)

        add_candidate(doc_path)
        if doc_path.suffix.lower() != ".md" or doc_path.suffix != ".md":
            add_candidate(doc_path.with_suffix(".md"))
        if doc_path.suffix.lower() != ".md":
            add_candidate(doc_path / "README.md")

        search_roots = [readme_base]
        if readme_base != root_base:
            search_roots.append(root_base)

        for relative in relative_candidates:
            for base in search_roots:
                base_resolved = base.resolve()
                candidate = (base_resolved / relative).resolve(strict=False)
                try:
                    candidate.relative_to(base_resolved)
                except ValueError:
                    continue
                candidates.append(candidate)
    else:
        default_readme = readme_base / "README.md"
        root_default: Path | None = None
        if lang:
            candidates.append(readme_base / f"README.{lang}.md")
            short = lang.split("-")[0]
            if short != lang:
                candidates.append(readme_base / f"README.{short}.md")
        if readme_base != root_base:
            candidates.append(default_readme)
            if lang:
                candidates.append(root_base / f"README.{lang}.md")
                short = lang.split("-")[0]
                if short != lang:
                    candidates.append(root_base / f"README.{short}.md")
            root_default = root_base / "README.md"
        else:
            root_default = default_readme
        locale_base = root_base / "locale"
        if locale_base.exists():
            if lang:
                candidates.append(locale_base / f"README.{lang}.md")
                short = lang.split("-")[0]
                if short != lang:
                    candidates.append(locale_base / f"README.{short}.md")
            candidates.append(locale_base / "README.md")
        if root_default is not None:
            candidates.append(root_default)

    readme_file = next((p for p in candidates if p.exists()), None)
    if readme_file is None:
        raise Http404("Document not found")

    title = "README" if readme_file.name.startswith("README") else readme_file.stem
    return SimpleNamespace(
        file=readme_file,
        title=title,
        root_base=root_base,
    )


def _split_html_sections(html: str, keep_sections: int) -> tuple[str, str]:
    """Return ``keep_sections`` leading sections and the remaining HTML."""

    if keep_sections < 1:
        return "", html

    heading_matches = list(re.finditer(r"<h[1-6]\\b[^>]*>", html, flags=re.IGNORECASE))
    if len(heading_matches) <= keep_sections:
        return html, ""

    split_index = heading_matches[keep_sections].start()
    return html[:split_index], html[split_index:]


def _normalize_docs_path(doc: str | None, prepend_docs: bool) -> str | None:
    if not doc or not prepend_docs:
        return doc
    if doc.startswith("docs/"):
        return doc
    return f"docs/{doc}"


def render_readme_page(
    request, *, doc: str | None = None, force_footer: bool = False, prepend_docs: bool = False, role=None
):
    lang = getattr(request, "LANGUAGE_CODE", "")
    lang = lang.replace("_", "-").lower()
    normalized_doc = _normalize_docs_path(doc, prepend_docs)
    if role is None:
        node = Node.get_local()
        role = node.role if node else None
    document = _locate_readme_document(role, normalized_doc, lang)
    html, toc_html = _render_document_file(document.file)
    full_document = request.GET.get("full") == "1"
    initial_content, remaining_content = _split_html_sections(html, 2)
    if full_document:
        initial_content = html
        remaining_content = ""

    if request.headers.get("HX-Request") == "true" and request.GET.get("fragment") == "remaining":
        response = HttpResponse(remaining_content)
        patch_vary_headers(response, ["Accept-Language", "Cookie"])
        return response
    base_query = request.GET.copy()
    base_query.pop("fragment", None)
    base_query.pop("full", None)
    fragment_query = base_query.copy()
    fragment_query["fragment"] = "remaining"
    fragment_url = f"{request.path}?{fragment_query.urlencode()}"
    full_query = base_query.copy()
    full_query["full"] = "1"
    full_document_url = f"{request.path}?{full_query.urlencode()}"
    context = {
        "content": initial_content,
        "title": document.title,
        "toc": toc_html,
        "has_remaining_sections": bool(remaining_content.strip()),
        "fragment_url": fragment_url,
        "full_document_url": full_document_url,
        "page_url": request.build_absolute_uri(),
        "force_footer": force_footer,
    }
    response = render(request, "docs/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@never_cache
def readme(request, doc=None, prepend_docs: bool = False):
    return render_readme_page(request, doc=doc, prepend_docs=prepend_docs)


def readme_asset(request, source: str, asset: str):
    source_normalized = (source or "").lower()
    if source_normalized == "static":
        file_path = _resolve_static_asset(asset)
    elif source_normalized == "work":
        file_path = _resolve_work_asset(getattr(request, "user", None), asset)
    else:
        raise Http404("Asset not found")

    if not file_path.exists() or not file_path.is_file():
        raise Http404("Asset not found")

    extension = file_path.suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise Http404("Asset not found")

    try:
        file_handle = file_path.open("rb")
    except OSError as exc:  # pragma: no cover - unexpected filesystem error
        logger.warning("Unable to open asset %s", file_path, exc_info=exc)
        raise Http404("Asset not found") from exc

    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    response = FileResponse(file_handle, content_type=content_type)
    try:
        response["Content-Length"] = str(file_path.stat().st_size)
    except OSError:  # pragma: no cover - filesystem race
        pass

    if source_normalized == "work":
        patch_cache_control(response, private=True, no_store=True)
        patch_vary_headers(response, ["Cookie"])
    else:
        patch_cache_control(response, public=True, max_age=3600)

    return response
