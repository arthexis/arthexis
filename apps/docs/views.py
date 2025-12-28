import logging
import mimetypes
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from apps.nodes.models import Node
from apps.modules.models import Module

from . import assets, rendering


logger = logging.getLogger(__name__)


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

        def add_localized_candidates(path: Path) -> None:
            if lang:
                if path.suffix:
                    add_candidate(path.with_name(f"{path.stem}.{lang}{path.suffix}"))
                    short = lang.split("-")[0]
                    if short and short != lang:
                        add_candidate(path.with_name(f"{path.stem}.{short}{path.suffix}"))
            add_candidate(path)

        add_localized_candidates(doc_path)
        if doc_path.suffix.lower() != ".md":
            add_localized_candidates(doc_path.with_suffix(".md"))
            add_localized_candidates(doc_path / "README.md")

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
    html, toc_html = rendering.render_document_file(document.file)
    full_document = request.GET.get("full") == "1"
    initial_content, remaining_content = rendering.split_html_sections(html, 2)
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
        file_path = assets.resolve_static_asset(asset)
    elif source_normalized == "work":
        file_path = assets.resolve_work_asset(getattr(request, "user", None), asset)
    else:
        raise Http404("Asset not found")

    if not file_path.exists() or not file_path.is_file():
        raise Http404("Asset not found")

    extension = file_path.suffix.lower()
    if extension not in assets.ALLOWED_IMAGE_EXTENSIONS:
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
