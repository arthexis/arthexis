"""README display and local reader-asset views."""

import logging
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlunsplit

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.modules.models import Module
from apps.nodes.models import Node
from apps.nodes.utils import FeatureChecker

from . import assets, rendering

logger = logging.getLogger(__name__)

README_NOT_FOUND_MESSAGE = "README not found"
README_FILENAME = "README.md"


def _localized_readme_candidates(base: Path, lang: str) -> list[Path]:
    """Return localized README candidates for one allowed directory."""

    candidates: list[Path] = []
    if lang:
        candidates.append(base / f"README.{lang}.md")
        short = lang.split("-")[0]
        if short != lang:
            candidates.append(base / f"README.{short}.md")
    candidates.append(base / README_FILENAME)
    return candidates


def _readme_base_for_module(root_base: Path, module_path: str) -> Path:
    """Return a module README base constrained to the suite root."""

    if not module_path:
        return root_base
    readme_base = (root_base / module_path).resolve()
    try:
        readme_base.relative_to(root_base)
    except ValueError:
        logger.warning("Ignoring README module path outside the suite root: %s", module_path)
        return root_base
    return readme_base


def _locate_readme_document(role, lang: str) -> SimpleNamespace:
    modules = (
        Module.objects.for_role(role)
        .filter(is_default=True, is_deleted=False)
        .select_related("application")
        .prefetch_related("features")
    )
    feature_checker = FeatureChecker()
    module = next(
        (
            candidate
            for candidate in modules
            if candidate.meets_feature_requirements(feature_checker.is_enabled)
        ),
        None,
    )
    module_path = module.path.strip("/") if module else ""
    root_base = Path(settings.BASE_DIR).resolve()
    readme_base = _readme_base_for_module(root_base, module_path)
    candidates = _localized_readme_candidates(readme_base, lang)
    if readme_base != root_base:
        candidates.extend(_localized_readme_candidates(root_base, lang))

    locale_base = root_base / "locale"
    if locale_base.exists():
        candidates.extend(_localized_readme_candidates(locale_base, lang))

    readme_file = next(
        (path for path in candidates if path.is_file() and path.suffix.lower() == ".md"),
        None,
    )
    if readme_file is None:
        raise Http404(README_NOT_FOUND_MESSAGE)
    return SimpleNamespace(file=readme_file, title="README")


def _render_document_cached(file_path: Path, cache_key: str) -> tuple[str, str]:
    cached = cache.get(cache_key)
    if cached:
        return cached
    html, toc_html = rendering.render_document_file(file_path)
    cache.set(cache_key, (html, toc_html), timeout=300)
    return html, toc_html


def _build_render_cache_key(file_path: Path, lang: str) -> str:
    try:
        mtime = int(file_path.stat().st_mtime)
    except OSError:
        mtime = 0
    return f"readme:render:{file_path}:{mtime}:{lang}"


def _build_canonical_url(request: HttpRequest, *, query: str = "") -> str:
    return urlunsplit((request.scheme, request.get_host(), request.path, query, ""))


def render_readme_page(request: HttpRequest, *, force_footer: bool = False, role=None):
    """Render the role-aware suite README without exposing a docs library."""

    lang = getattr(request, "LANGUAGE_CODE", "").replace("_", "-").lower()
    if role is None:
        node = Node.get_local()
        role = node.role if node else None
    document = _locate_readme_document(role, lang)
    cache_key = _build_render_cache_key(document.file, lang)
    is_authenticated = getattr(request, "user", None) and request.user.is_authenticated
    if is_authenticated:
        html, toc_html = rendering.render_document_file(document.file)
    else:
        html, toc_html = _render_document_cached(document.file, cache_key)

    context = {
        "canonical_url": _build_canonical_url(request),
        "content": html,
        "title": document.title,
        "toc": toc_html,
        "page_url": request.build_absolute_uri(),
        "force_footer": force_footer,
    }
    response = render(request, "docs/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@never_cache
@require_GET
def readme(request: HttpRequest):
    """Serve the retained public README display route."""

    return render_readme_page(request)


@require_GET
def readme_asset(request: HttpRequest, source: str, asset: str):
    """Serve the local image assets referenced by the README renderer."""

    source_normalized = (source or "").lower()
    if source_normalized == "static":
        file_path = assets.resolve_static_asset(asset)
    elif source_normalized == "work":
        file_path = assets.resolve_work_asset(getattr(request, "user", None), asset)
    else:
        raise Http404("Asset not found")

    if not file_path.is_file() or file_path.suffix.lower() not in assets.ALLOWED_IMAGE_EXTENSIONS:
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
