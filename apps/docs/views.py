import logging
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode, urlunsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.http import FileResponse, Http404, HttpResponse
from django.urls import NoReverseMatch, reverse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from apps.nodes.models import Node
from apps.nodes.utils import FeatureChecker
from apps.modules.models import Module
from apps.sites.utils import module_pill_link_validation

from .models import DocumentIndex
from . import assets, rendering


logger = logging.getLogger(__name__)

ALLOWED_DOC_EXTENSIONS = (
    rendering.MARKDOWN_FILE_EXTENSIONS
    | rendering.PLAINTEXT_FILE_EXTENSIONS
    | rendering.CSV_FILE_EXTENSIONS
    | {".rst"}
)
DOCUMENT_NOT_FOUND_MESSAGE = "Document not found"
DOCUMENT_LIBRARY_CACHE_KEY = "docs:library:index"
DOCUMENT_LIBRARY_CACHE_TIMEOUT = 300
DOCS_CANONICAL_HOST_OVERRIDES = {
    "m.arthexis.com": "arthexis.com",
}
LIBRARY_ROOT_FOLDER_LABEL = "root"
LIBRARY_ROOT_QUERY_PARAMETER = "virtual_root"
FULL_CONTENT_DEFAULT_DOCUMENTS = {
    "docs/development/install-lifecycle-scripts-manual.md",
}


def _show_docs_navigation_link(*, request, landing) -> bool:
    del landing
    return bool(getattr(request, "user", None) and request.user.is_authenticated)


def _is_allowed_doc_path(path: Path) -> bool:
    return path.suffix.lower() in ALLOWED_DOC_EXTENSIONS


def _locate_readme_document(role, doc: str | None, lang: str) -> SimpleNamespace:
    modules = (
        Module.objects.for_role(role)
        .filter(is_default=True, is_deleted=False)
        .select_related("application")
        .prefetch_related("features")
    )
    feature_checker = FeatureChecker()

    app = next(
        (
            module
            for module in modules
            if module.meets_feature_requirements(feature_checker.is_enabled)
        ),
        None,
    )
    app_slug = app.path.strip("/") if app else ""
    root_base = Path(settings.BASE_DIR).resolve()
    locale_docs_base = root_base / "apps" / "locale" / "docs"
    docs_app_base = root_base / "apps" / "docs"
    readme_base = (root_base / app_slug).resolve() if app_slug else root_base
    candidates: list[Path] = []

    if doc:
        normalized = doc.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        if not normalized:
            raise Http404(DOCUMENT_NOT_FOUND_MESSAGE)
        doc_path = Path(normalized)
        if doc_path.is_absolute() or any(part == ".." for part in doc_path.parts):
            raise Http404(DOCUMENT_NOT_FOUND_MESSAGE)

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

        search_roots: list[Path] = []
        if normalized.startswith(("docs/", "apps/docs/", "apps/locale/docs/")):
            search_roots.append(root_base)
        if docs_app_base.exists() and not normalized.startswith("apps/docs/"):
            search_roots.append(docs_app_base)
        if locale_docs_base.exists() and not normalized.startswith("apps/locale/docs/"):
            search_roots.append(locale_docs_base)

        for relative in relative_candidates:
            for base in search_roots:
                base_resolved = base.resolve()
                candidate = (base_resolved / relative).resolve(strict=False)
                try:
                    candidate.relative_to(base_resolved)
                except ValueError:
                    continue
                if not _is_allowed_doc_path(candidate):
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
        if locale_docs_base.exists():
            if lang:
                candidates.append(locale_docs_base / f"README.{lang}.md")
                short = lang.split("-")[0]
                if short != lang:
                    candidates.append(locale_docs_base / f"README.{short}.md")
            candidates.append(locale_docs_base / "README.md")
        if root_default is not None:
            candidates.append(root_default)

    readme_file = next(
        (p for p in candidates if p.exists() and p.is_file() and _is_allowed_doc_path(p)),
        None,
    )
    if readme_file is None:
        raise Http404(DOCUMENT_NOT_FOUND_MESSAGE)

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
    return f"docs:render:{file_path}:{mtime}:{lang}"


def _iter_document_paths(root: Path) -> list[Path]:
    """Return allowed documentation files under ``root``."""

    if not root.exists():
        return []
    documents: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not _is_allowed_doc_path(path):
            continue
        documents.append(path)
    return sorted(documents)


def _extract_document_blurb(path: Path, *, max_length: int = 220) -> str:
    """Return a short summary line for the library index entry at ``path``."""

    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    candidates: list[str] = []
    in_front_matter = False
    seen_non_blank = False
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "---":
            if in_front_matter or not seen_non_blank:
                in_front_matter = not in_front_matter
                continue
        seen_non_blank = True
        if in_front_matter:
            continue
        if stripped.startswith("#"):
            continue
        if stripped == "---":
            continue
        candidates.append(stripped)
        if len(candidates) >= 3:
            break

    if not candidates:
        return ""

    summary = " ".join(candidates)
    if len(summary) <= max_length:
        return summary

    cut_off = summary.rfind(" ", 0, max_length)
    if cut_off == -1:
        cut_off = max_length - 1

    return f"{summary[:cut_off].rstrip()}…"


def _build_library_item(
    path: Path,
    root: Path,
    route_name: str,
    *,
    doc_path_prefix: str = "",
    label: str | None = None,
) -> dict[str, str]:
    """Build a single document-library item with URL and blurb metadata."""

    relative = path.relative_to(root).as_posix()
    description = _extract_document_blurb(path)
    if not description:
        description = f"Reference documentation for {relative}."
    try:
        url = reverse(route_name, args=[relative])
    except NoReverseMatch:
        logger.warning("Unable to reverse %r for %s", route_name, relative)
        url = ""
    stored_doc_path = f"{doc_path_prefix}{relative}" if doc_path_prefix else relative
    return {
        "doc_path": stored_doc_path,
        "label": label or relative,
        "url": url,
        "description": description,
        "kind": "document",
    }


def _normalize_library_prefix(prefix: str | None) -> str:
    """Normalize an incoming library folder prefix."""

    if not prefix:
        return ""
    normalized = prefix.replace("\\", "/").strip("/")
    if not normalized:
        return ""
    parts = [part for part in normalized.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts)


def _build_library_query_url(parameter: str, prefix: str) -> str:
    query = urlencode({parameter: prefix}) if prefix else ""
    return f"{reverse('docs:docs-library')}?{query}" if query else reverse("docs:docs-library")


def _build_virtual_root_query_url(parameter: str) -> str:
    return f"{reverse('docs:docs-library')}?{urlencode({parameter: '1'})}"


def _pluralize(count: int, singular: str) -> str:
    """Return the singular/plural form for a count."""

    return singular if count == 1 else f"{singular}s"


def _preview_files(names: list[str]) -> tuple[str, str]:
    """Build a deterministic two-item preview and overflow suffix."""

    preview = ", ".join(sorted(names)[:2])
    suffix = "…" if len(names) > 2 else ""
    return preview, suffix


def _build_folder_blurb(direct_files: list[str], nested_count: int) -> str:
    """Return a short, data-backed summary for a folder entry."""

    direct_count = len(direct_files)
    total_documents = direct_count + nested_count
    if total_documents == 0:
        return "Folder overview and related references."

    if direct_count == 0:
        return f"{nested_count} nested {_pluralize(nested_count, 'folder')} with additional documentation."

    preview, suffix = _preview_files(direct_files)
    if nested_count:
        return (
            f"{direct_count} {_pluralize(direct_count, 'doc')} ({preview}{suffix}) "
            f"and {nested_count} nested {_pluralize(nested_count, 'folder')}."
        )
    return f"{direct_count} {_pluralize(direct_count, 'doc')}: {preview}{suffix}."


def _build_library_section(
    files: list[Path],
    *,
    root: Path,
    route_name: str,
    doc_path_prefix: str,
    title: str,
    prefix: str,
    parameter: str,
    virtual_root_selected: bool,
) -> dict[str, object]:
    """Build a section index scoped to one folder level."""

    folders: set[str] = set()
    items: list[dict[str, str]] = []
    root_items: list[dict[str, str]] = []
    show_root_folder = prefix == ""
    in_virtual_root_folder = virtual_root_selected and prefix == ""

    for path in files:
        relative = path.relative_to(root).as_posix()
        if in_virtual_root_folder:
            if "/" in relative or path.stem.lower() == "index":
                continue
            items.append(
                _build_library_item(
                    path,
                    root,
                    route_name,
                    doc_path_prefix=doc_path_prefix,
                    label=Path(relative).name,
                )
            )
            continue
        if prefix:
            if relative == prefix:
                if path.stem.lower() == "index":
                    continue
                items.append(
                    _build_library_item(
                        path,
                        root,
                        route_name,
                        doc_path_prefix=doc_path_prefix,
                        label=Path(relative).name,
                    )
                )
                continue
            if not relative.startswith(f"{prefix}/"):
                continue
            scoped_relative = relative.removeprefix(f"{prefix}/")
        else:
            scoped_relative = relative

        if "/" in scoped_relative:
            folders.add(scoped_relative.split("/", 1)[0])
            continue
        if path.stem.lower() == "index":
            continue
        root_items.append(
            _build_library_item(
                path,
                root,
                route_name,
                doc_path_prefix=doc_path_prefix,
                label=Path(relative).name,
            )
        )

    folder_direct_files: dict[str, list[str]] = {}
    folder_nested_folders: dict[str, dict[str, bool]] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        if prefix:
            prefix_root = f"{prefix}/"
            if not relative.startswith(prefix_root):
                continue
            scoped_relative = relative.removeprefix(prefix_root)
        else:
            scoped_relative = relative
        if "/" not in scoped_relative:
            continue

        folder, remainder = scoped_relative.split("/", 1)
        direct_files = folder_direct_files.setdefault(folder, [])
        nested_folders = folder_nested_folders.setdefault(folder, {})
        if "/" in remainder:
            nested_folder = remainder.split("/", 1)[0]
            if Path(remainder).stem.lower() != "index":
                nested_folders[nested_folder] = True
            else:
                nested_folders.setdefault(nested_folder, False)
            continue
        if path.stem.lower() == "index":
            continue
        direct_files.append(Path(remainder).name)

    folder_items = [
        {
            "kind": "folder",
            "label": f"{folder}/",
            "url": _build_library_query_url(
                parameter,
                f"{prefix}/{folder}" if prefix else folder,
            ),
            "description": _build_folder_blurb(
                folder_direct_files.get(folder, []),
                sum(folder_nested_folders.get(folder, {}).values()),
            ),
        }
        for folder in sorted(folders)
    ]
    if show_root_folder and root_items:
        folder_items.insert(
            0,
            {
                "kind": "folder",
                "label": f"{LIBRARY_ROOT_FOLDER_LABEL}/",
                "url": _build_virtual_root_query_url(f"{parameter}_{LIBRARY_ROOT_QUERY_PARAMETER}"),
                "description": "Browse root-level documents.",
            },
        )

    folder_items.extend(item for item in items if item["url"])
    if prefix != "":
        folder_items.extend(item for item in root_items if item["url"])

    section: dict[str, object] = {
        "title": title,
        "items": folder_items,
    }
    if prefix:
        parent_prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        if in_virtual_root_folder:
            parent_prefix = ""
        section["current_prefix"] = prefix
        section["parent_url"] = _build_library_query_url(parameter, parent_prefix)
    elif in_virtual_root_folder:
        section["current_prefix"] = LIBRARY_ROOT_FOLDER_LABEL
        section["parent_url"] = _build_library_query_url(parameter, "")
    return section


def _collect_document_library(
    root_base: Path,
    *,
    docs_prefix: str = "",
    apps_docs_prefix: str = "",
    docs_virtual_root_selected: bool = False,
    apps_docs_virtual_root_selected: bool = False,
    docs_files: list[Path] | None = None,
    apps_docs_files: list[Path] | None = None,
) -> list[dict[str, object]]:
    """Build a flat library index for docs and apps/docs content."""

    docs_root = root_base / "docs"
    apps_docs_root = root_base / "apps" / "docs"
    sections: list[dict[str, object]] = []

    docs_files = docs_files if docs_files is not None else _iter_document_paths(docs_root)
    if docs_files:
        sections.append(
            _build_library_section(
                docs_files,
                root=docs_root,
                route_name="docs:docs-document",
                doc_path_prefix="docs/",
                title="Documentation",
                prefix=_normalize_library_prefix(docs_prefix),
                parameter="docs_path",
                virtual_root_selected=docs_virtual_root_selected,
            )
        )

    apps_docs_files = (
        apps_docs_files if apps_docs_files is not None else _iter_document_paths(apps_docs_root)
    )
    if apps_docs_files:
        sections.append(
            _build_library_section(
                apps_docs_files,
                root=apps_docs_root,
                route_name="docs:apps-docs-document",
                doc_path_prefix="apps/docs/",
                title="Application Docs",
                prefix=_normalize_library_prefix(apps_docs_prefix),
                parameter="apps_docs_path",
                virtual_root_selected=apps_docs_virtual_root_selected,
            )
        )

    return sections


def _build_library_documents(
    files: list[Path],
    *,
    root: Path,
    route_name: str,
    doc_path_prefix: str,
) -> list[dict[str, str]]:
    """Build all document entries for indexed grouping and fallback listing."""

    documents: list[dict[str, str]] = []
    for path in files:
        if path.stem.lower() == "index":
            continue
        documents.append(
            _build_library_item(
                path,
                root,
                route_name,
                doc_path_prefix=doc_path_prefix,
                label=path.name,
            )
        )
    return [item for item in documents if item["url"]]


def _build_indexed_document_groups(request, documents: list[dict[str, str]]) -> list[dict[str, object]]:
    """Group indexed documents by security-group course (and listable catch-all)."""

    document_by_path = {item["doc_path"]: item for item in documents}
    indexed_documents = (
        DocumentIndex.objects.filter(doc_path__in=document_by_path.keys())
        .prefetch_related("assignments__security_group")
        .order_by("title", "doc_path")
    )
    user_group_ids = set(request.user.groups.values_list("id", flat=True))
    grouped: dict[str, dict[str, object]] = {}

    def ensure_group(title: str, *, description: str = "") -> dict[str, object]:
        if title not in grouped:
            grouped[title] = {"title": title, "description": description, "items": []}
        return grouped[title]

    for indexed in indexed_documents:
        item = document_by_path.get(indexed.doc_path)
        if not item:
            continue

        has_assignments = False
        has_visible_assignment = False
        for assignment in indexed.assignments.all():
            has_assignments = True
            if (
                assignment.access == DocumentIndex.ACCESS_RESTRICTED
                and assignment.security_group_id not in user_group_ids
            ):
                continue
            has_visible_assignment = True
            group = ensure_group(
                assignment.security_group.name,
                description="Course",
            )
            group["items"].append(
                {
                    **item,
                    "access": assignment.get_access_display(),
                }
            )

        if indexed.listable and not has_assignments and not has_visible_assignment:
            group = ensure_group("Listable", description="General")
            group["items"].append({**item, "access": "Available"})

    return [value for _, value in sorted(grouped.items(), key=lambda pair: pair[0].lower())]


def _get_cached_document_library_paths(root_base: Path) -> tuple[list[Path], list[Path]]:
    """Return cached document path lists to avoid repeated filesystem scans."""

    cache_key = f"{DOCUMENT_LIBRARY_CACHE_KEY}:paths:{root_base.as_posix()}"
    cached_paths = cache.get(cache_key)
    if cached_paths is not None:
        docs_paths = [Path(path) for path in cached_paths["docs"]]
        apps_docs_paths = [Path(path) for path in cached_paths["apps_docs"]]
        return docs_paths, apps_docs_paths

    docs_root = root_base / "docs"
    apps_docs_root = root_base / "apps" / "docs"
    docs_paths = _iter_document_paths(docs_root)
    apps_docs_paths = _iter_document_paths(apps_docs_root)
    cache.set(
        cache_key,
        {
            "docs": [path.as_posix() for path in docs_paths],
            "apps_docs": [path.as_posix() for path in apps_docs_paths],
        },
        timeout=DOCUMENT_LIBRARY_CACHE_TIMEOUT,
    )
    return docs_paths, apps_docs_paths


def _get_cached_document_library(
    root_base: Path,
    *,
    docs_prefix: str = "",
    apps_docs_prefix: str = "",
    docs_virtual_root_selected: bool = False,
    apps_docs_virtual_root_selected: bool = False,
) -> list[dict[str, object]]:
    """Return a cached library index to avoid repeated filesystem scans."""

    docs_paths, apps_docs_paths = _get_cached_document_library_paths(root_base)
    return _collect_document_library(
        root_base,
        docs_prefix=docs_prefix,
        apps_docs_prefix=apps_docs_prefix,
        docs_virtual_root_selected=docs_virtual_root_selected,
        apps_docs_virtual_root_selected=apps_docs_virtual_root_selected,
        docs_files=docs_paths,
        apps_docs_files=apps_docs_paths,
    )


def _render_document_library(
    request,
    *,
    status: int = 200,
    missing_document: str | None = None,
) -> HttpResponse:
    """Render the docs library page for both standard and fallback flows."""

    root_base = Path(settings.BASE_DIR).resolve()
    docs_prefix = request.GET.get("docs_path", "")
    apps_docs_prefix = request.GET.get("apps_docs_path", "")
    docs_virtual_root_selected = request.GET.get(f"docs_path_{LIBRARY_ROOT_QUERY_PARAMETER}") == "1"
    apps_docs_virtual_root_selected = (
        request.GET.get(f"apps_docs_path_{LIBRARY_ROOT_QUERY_PARAMETER}") == "1"
    )
    sections = _get_cached_document_library(
        root_base,
        docs_prefix=docs_prefix,
        apps_docs_prefix=apps_docs_prefix,
        docs_virtual_root_selected=docs_virtual_root_selected,
        apps_docs_virtual_root_selected=apps_docs_virtual_root_selected,
    )
    docs_paths, apps_docs_paths = _get_cached_document_library_paths(root_base)
    all_documents = _build_library_documents(
        docs_paths,
        root=root_base / "docs",
        route_name="docs:docs-document",
        doc_path_prefix="docs/",
    ) + _build_library_documents(
        apps_docs_paths,
        root=root_base / "apps" / "docs",
        route_name="docs:apps-docs-document",
        doc_path_prefix="apps/docs/",
    )
    indexed_groups = _build_indexed_document_groups(request, all_documents)
    context = {
        "canonical_url": _build_canonical_url(request),
        "document_index_admin_add_url": "",
        "document_index_admin_changelist_url": "",
        "indexed_groups": indexed_groups,
        "page_url": request.build_absolute_uri(),
        "sections": sections,
        "title": "Developer Documents",
    }
    if request.user.is_staff:
        try:
            context["document_index_admin_changelist_url"] = reverse(
                "admin:docs_documentindex_changelist"
            )
            context["document_index_admin_add_url"] = reverse("admin:docs_documentindex_add")
        except NoReverseMatch:
            pass
    if missing_document:
        context["missing_document"] = missing_document
    response = render(request, "docs/library.html", context, status=status)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


def _canonicalize_docs_host(host: str) -> str:
    """Return the canonical docs host when the request host uses an alias."""

    return DOCS_CANONICAL_HOST_OVERRIDES.get(host, host)


def _build_canonical_url(request, *, path: str | None = None, query: str = "") -> str:
    """Build a canonical URL for docs pages with stable host normalization."""

    host = _canonicalize_docs_host(request.get_host())
    target_path = path or request.path
    return urlunsplit((request.scheme, host, target_path, query, ""))


def _should_default_full_document(doc: str | None) -> bool:
    """Return whether a document should render full content by default."""

    if not doc:
        return False
    normalized = doc.strip().replace("\\", "/").lstrip("/")
    if normalized in FULL_CONTENT_DEFAULT_DOCUMENTS:
        return True
    if Path(normalized).suffix:
        return False
    return f"{normalized}.md" in FULL_CONTENT_DEFAULT_DOCUMENTS


def render_readme_page(
    request,
    *,
    doc: str | None = None,
    force_footer: bool = False,
    prepend_docs: bool = False,
    role=None,
):
    lang = getattr(request, "LANGUAGE_CODE", "")
    lang = lang.replace("_", "-").lower()
    normalized_doc = _normalize_docs_path(doc, prepend_docs)
    if role is None:
        node = Node.get_local()
        role = node.role if node else None
    document = _locate_readme_document(role, normalized_doc, lang)
    cache_key = _build_render_cache_key(document.file, lang)
    is_authenticated = getattr(request, "user", None) and request.user.is_authenticated
    if is_authenticated:
        html, toc_html = rendering.render_document_file(document.file)
    else:
        html, toc_html = _render_document_cached(document.file, cache_key)
    force_full_document = _should_default_full_document(normalized_doc)
    full_document = request.GET.get("full") == "1" or (
        force_full_document and "full" not in request.GET
    )
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
    full_query_string = full_query.urlencode()
    full_document_url = f"{request.path}?{full_query_string}"
    canonical_query = full_query_string if force_full_document else ""
    canonical_url = _build_canonical_url(
        request,
        path=request.path,
        query=canonical_query,
    )
    context = {
        "canonical_url": canonical_url,
        "content": initial_content,
        "title": document.title,
        "toc": toc_html,
        "has_remaining_sections": bool(remaining_content.strip()),
        "fragment_url": fragment_url,
        "full_document_url": full_document_url,
        "page_url": request.build_absolute_uri(),
        "force_footer": force_footer,
        "current_document_path": str(document.file),
    }
    response = render(request, "docs/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@module_pill_link_validation(_show_docs_navigation_link)
@login_required(login_url="pages:login")
def document_library(request):
    """Render the developer documentation library index."""

    return _render_document_library(request)


def _render_missing_document(request, *, doc: str | None, prepend_docs: bool) -> HttpResponse:
    """Render a helpful fallback page when a documentation path is missing."""

    missing_path = _normalize_docs_path(doc, prepend_docs) or ""
    return _render_document_library(request, status=404, missing_document=missing_path)


@module_pill_link_validation(_show_docs_navigation_link)
@never_cache
@login_required(login_url="pages:login")
def readme(request, doc=None, prepend_docs: bool = False):
    try:
        return render_readme_page(request, doc=doc, prepend_docs=prepend_docs)
    except Http404 as exc:
        message = str(exc)
        if message == DOCUMENT_NOT_FOUND_MESSAGE:
            return _render_missing_document(request, doc=doc, prepend_docs=prepend_docs)
        raise


@login_required(login_url="pages:login")
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
