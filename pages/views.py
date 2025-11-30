import base64
import csv
import json
import logging
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
import datetime
import calendar
import io
import shutil
import re
from typing import Any
from html import escape
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.sites.models import Site
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django import forms
from django.apps import apps as django_apps
from utils.decorators import security_group_required
from utils.sites import get_site
from django.contrib.staticfiles import finders
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from nodes.models import Node
from nodes.utils import capture_screenshot, save_screenshot
from django.template import loader
from django.template.response import TemplateResponse
from django.test import RequestFactory, signals as test_signals
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import (
    url_has_allowed_host_and_scheme,
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)
from django_otp import DEVICE_ID_SESSION_KEY
from apps.core import changelog, mailer, passkeys, public_wifi
from apps.core.templatetags.ref_tags import build_footer_context
from apps.core.backends import (
    TOTP_DEVICE_NAME,
    get_user_totp_devices,
    totp_devices_allow_passwordless,
    totp_devices_require_password,
)
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt, csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from django.core.cache import cache
from django.views.decorators.cache import never_cache
from django.utils.cache import patch_cache_control, patch_vary_headers
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.utils.text import slugify, Truncator
from django.core.validators import EmailValidator
from django.db.models import Q
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from accounts.models import ClientReport, ClientReportSchedule
from apps.core.models import InviteLead, PasskeyCredential, SecurityGroup
from apps.ocpp.models import Charger
from .utils import get_original_referer, get_request_language_code


class _GraphvizDeprecationFilter(logging.Filter):
    """Filter out Graphviz debug logs about positional arg deprecations."""

    _MESSAGE_PREFIX = "deprecate positional args:"

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - logging hook
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive fallback
            return True
        return not message.startswith(self._MESSAGE_PREFIX)


try:  # pragma: no cover - optional dependency guard
    from graphviz import Digraph
    from graphviz.backend import CalledProcessError, ExecutableNotFound
except ImportError:  # pragma: no cover - handled gracefully in views
    Digraph = None
    CalledProcessError = ExecutableNotFound = None
else:
    graphviz_logger = logging.getLogger("graphviz._tools")
    if not any(
        isinstance(existing_filter, _GraphvizDeprecationFilter)
        for existing_filter in graphviz_logger.filters
    ):
        graphviz_logger.addFilter(_GraphvizDeprecationFilter())

import markdown
from django.utils._os import safe_join


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

PASSKEY_LOGIN_SESSION_KEY = "pages.passkeys.login"
PASSKEY_REGISTRATION_SESSION_KEY = "pages.passkeys.registration"
PASSKEY_NAME_MAX_LENGTH = PasskeyCredential._meta.get_field("name").max_length


def _render_markdown_with_toc(text: str) -> tuple[str, str]:
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
        return _render_markdown_with_toc(text)
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
                "pages:readme-asset",
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
from pages.utils import landing
from apps.core.liveupdate import live_update
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
import qrcode
from .forms import (
    AuthenticatorEnrollmentForm,
    AuthenticatorLoginForm,
    UserStoryForm,
)
from .models import (
    ChatSession,
    DeveloperArticle,
    Module,
    RoleLanding,
    UserManual,
    UserStory,
)


logger = logging.getLogger(__name__)


def _get_registered_models(app_label: str):
    """Return admin-registered models for the given app label."""

    registered = [
        model for model in admin.site._registry if model._meta.app_label == app_label
    ]
    return sorted(registered, key=lambda model: str(model._meta.verbose_name))


def _get_client_ip(request) -> str:
    """Return the client IP from the request headers."""

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        for value in forwarded_for.split(","):
            candidate = value.strip()
            if candidate:
                return candidate
    return request.META.get("REMOTE_ADDR", "")


def _filter_models_for_request(models, request):
    """Filter ``models`` to only those viewable by ``request.user``."""

    allowed = []
    for model in models:
        model_admin = admin.site._registry.get(model)
        if model_admin is None:
            continue
        if not model_admin.has_module_permission(request) and not getattr(
            request.user, "is_staff", False
        ):
            continue
        if not model_admin.has_view_permission(request, obj=None) and not getattr(
            request.user, "is_staff", False
        ):
            continue
        allowed.append(model)
    return allowed


def _admin_has_app_permission(request, app_label: str) -> bool:
    """Return whether the admin user can access the given app."""

    has_app_permission = getattr(admin.site, "has_app_permission", None)
    if callable(has_app_permission):
        allowed = has_app_permission(request, app_label)
    else:
        allowed = bool(admin.site.get_app_list(request, app_label))

    if not allowed and getattr(request.user, "is_staff", False):
        return True
    return allowed


def _resolve_related_model(field, default_app_label: str):
    """Resolve the Django model class referenced by ``field``."""

    remote = getattr(getattr(field, "remote_field", None), "model", None)
    if remote is None:
        return None
    if isinstance(remote, str):
        if "." in remote:
            app_label, model_name = remote.split(".", 1)
        else:
            app_label, model_name = default_app_label, remote
        try:
            remote = django_apps.get_model(app_label, model_name)
        except LookupError:
            return None
    return remote


def _graph_field_type(field, default_app_label: str) -> str:
    """Format a field description for node labels."""

    base = field.get_internal_type()
    related = _resolve_related_model(field, default_app_label)
    if related is not None:
        base = f"{base} → {related._meta.object_name}"
    return base


def _build_model_graph(models):
    """Generate a GraphViz ``Digraph`` for the provided ``models``."""

    if Digraph is None:
        raise RuntimeError("Graphviz is not installed")

    graph = Digraph(
        name="admin_app_models",
        graph_attr={
            "rankdir": "LR",
            "splines": "ortho",
            "nodesep": "0.8",
            "ranksep": "1.0",
        },
        node_attr={
            "shape": "plaintext",
            "fontname": "Helvetica",
        },
        edge_attr={"fontname": "Helvetica"},
    )

    node_ids = {}
    for model in models:
        node_id = f"{model._meta.app_label}.{model._meta.model_name}"
        node_ids[model] = node_id

        rows = [
            '<tr><td bgcolor="#1f2933" colspan="2"><font color="white"><b>'
            f"{escape(model._meta.object_name)}"
            "</b></font></td></tr>"
        ]

        verbose_name = str(model._meta.verbose_name)
        if verbose_name and verbose_name != model._meta.object_name:
            rows.append(
                '<tr><td colspan="2"><i>' f"{escape(verbose_name)}" "</i></td></tr>"
            )

        for field in model._meta.concrete_fields:
            if field.auto_created and not field.concrete:
                continue
            name = escape(field.name)
            if field.primary_key:
                name = f"<u>{name}</u>"
            type_label = escape(_graph_field_type(field, model._meta.app_label))
            rows.append(
                '<tr><td align="left">'
                f"{name}"
                '</td><td align="left">'
                f"{type_label}"
                "</td></tr>"
            )

        for field in model._meta.local_many_to_many:
            name = escape(field.name)
            type_label = _graph_field_type(field, model._meta.app_label)
            rows.append(
                '<tr><td align="left">'
                f"{name}"
                '</td><td align="left">'
                f"{escape(type_label)}"
                "</td></tr>"
            )

        label = '<\n  <table BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">\n    '
        label += "\n    ".join(rows)
        label += "\n  </table>\n>"
        graph.node(name=node_id, label=label)

    edges = set()
    for model in models:
        source_id = node_ids[model]
        for field in model._meta.concrete_fields:
            related = _resolve_related_model(field, model._meta.app_label)
            if related not in node_ids:
                continue
            attrs = {"label": field.name}
            if getattr(field, "one_to_one", False):
                attrs.update({"arrowhead": "onormal", "arrowtail": "none"})
            key = (source_id, node_ids[related], tuple(sorted(attrs.items())))
            if key not in edges:
                edges.add(key)
                graph.edge(
                    tail_name=source_id,
                    head_name=node_ids[related],
                    **attrs,
                )

        for field in model._meta.local_many_to_many:
            related = _resolve_related_model(field, model._meta.app_label)
            if related not in node_ids:
                continue
            attrs = {
                "label": f"{field.name} (M2M)",
                "dir": "both",
                "arrowhead": "normal",
                "arrowtail": "normal",
            }
            key = (source_id, node_ids[related], tuple(sorted(attrs.items())))
            if key not in edges:
                edges.add(key)
                graph.edge(
                    tail_name=source_id,
                    head_name=node_ids[related],
                    **attrs,
                )

    return graph


@staff_member_required
def admin_model_graph(request, app_label: str):
    """Render a GraphViz-powered diagram for the admin app grouping."""

    try:
        app_config = django_apps.get_app_config(app_label)
    except LookupError as exc:  # pragma: no cover - invalid app label
        raise Http404("Unknown application") from exc

    models = _get_registered_models(app_label)
    if not models:
        raise Http404("No admin models registered for this application")

    if not _admin_has_app_permission(request, app_label):
        raise PermissionDenied

    models = _filter_models_for_request(models, request)
    if not models:
        raise PermissionDenied

    if Digraph is None:  # pragma: no cover - dependency missing is unexpected
        raise Http404("Graph visualization support is unavailable")

    graph = _build_model_graph(models)
    graph_source = graph.source

    graph_svg = ""
    graph_error = ""
    graph_engine = getattr(graph, "engine", "dot")
    engine_path = shutil.which(str(graph_engine))
    download_format = request.GET.get("format")

    if download_format == "pdf":
        if engine_path is None:
            messages.error(
                request,
                _(
                    "Graphviz executables are required to download the diagram as a PDF. Install Graphviz on the server and try again."
                ),
            )
        else:
            try:
                pdf_output = graph.pipe(format="pdf")
            except (ExecutableNotFound, CalledProcessError) as exc:
                logger.warning(
                    "Graphviz PDF rendering failed for admin model graph (engine=%s)",
                    graph_engine,
                    exc_info=exc,
                )
                messages.error(
                    request,
                    _(
                        "An error occurred while generating the PDF diagram. Check the server logs for details."
                    ),
                )
            else:
                filename = slugify(app_config.verbose_name) or app_label
                response = HttpResponse(pdf_output, content_type="application/pdf")
                response["Content-Disposition"] = (
                    f'attachment; filename="{filename}-model-graph.pdf"'
                )
                return response

        params = request.GET.copy()
        if "format" in params:
            del params["format"]
        query_string = params.urlencode()
        redirect_url = request.path
        if query_string:
            redirect_url = f"{request.path}?{query_string}"
        return redirect(redirect_url)

    if engine_path is None:
        graph_error = _(
            "Graphviz executables are required to render this diagram. Install Graphviz on the server and try again."
        )
    else:
        try:
            svg_output = graph.pipe(format="svg", encoding="utf-8")
        except (ExecutableNotFound, CalledProcessError) as exc:
            logger.warning(
                "Graphviz rendering failed for admin model graph (engine=%s)",
                graph_engine,
                exc_info=exc,
            )
            graph_error = _(
                "An error occurred while rendering the diagram. Check the server logs for details."
            )
        else:
            svg_start = svg_output.find("<svg")
            if svg_start != -1:
                svg_output = svg_output[svg_start:]
            label = _("%(app)s model diagram") % {"app": app_config.verbose_name}
            graph_svg = svg_output.replace(
                "<svg", f'<svg role="img" aria-label="{escape(label)}"', 1
            )
            if not graph_svg:
                graph_error = _("Graphviz did not return any diagram output.")

    model_links = []
    for model in models:
        opts = model._meta
        try:
            url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
        except NoReverseMatch:
            url = ""
        model_links.append(
            {
                "label": str(opts.verbose_name_plural),
                "url": url,
            }
        )

    download_params = request.GET.copy()
    download_params["format"] = "pdf"
    download_url = f"{request.path}?{download_params.urlencode()}"

    extra_context = {
        "app_label": app_label,
        "app_verbose_name": app_config.verbose_name,
        "graph_source": graph_source,
        "graph_svg": graph_svg,
        "graph_error": graph_error,
        "models": model_links,
        "title": _("%(app)s model graph") % {"app": app_config.verbose_name},
        "download_url": download_url,
    }

    return _render_admin_template(
        request,
        "admin/model_graph.html",
        extra_context,
    )


def _locate_readme_document(role, doc: str | None, lang: str) -> SimpleNamespace:
    app = (
        Module.objects.filter(node_role=role, is_default=True)
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


def _render_readme(request, role, doc: str | None = None, force_footer: bool = False):
    lang = getattr(request, "LANGUAGE_CODE", "")
    lang = lang.replace("_", "-").lower()
    document = _locate_readme_document(role, doc, lang)
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
    response = render(request, "pages/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@require_GET
def footer_fragment(request):
    """Return the footer markup for lazy-loading via HTMX."""

    force_footer = request.GET.get("force") in {"1", "true", "True"}
    context = build_footer_context(
        request=request,
        badge_site=getattr(request, "badge_site", None),
        badge_node=getattr(request, "badge_node", None),
        force_footer=force_footer,
    )
    return TemplateResponse(request, "core/footer.html", context)


def readme_docs_redirect(request, doc: str | None = None):
    """Redirect ``/docs`` requests to the canonical reader endpoints."""

    if doc:
        normalized_doc = doc
        if not normalized_doc.startswith("docs/"):
            normalized_doc = f"docs/{normalized_doc}"
        target = reverse("pages:readme-document", kwargs={"doc": normalized_doc})
    else:
        target = reverse("pages:readme")

    query_string = request.META.get("QUERY_STRING")
    if query_string:
        target = f"{target}?{query_string}"

    return HttpResponseRedirect(target)


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


@landing("Home")
@never_cache
def index(request):
    site = get_site(request)
    if site:
        try:
            landing = site.badge.landing_override
        except Exception:
            landing = None
        if landing:
            return redirect(landing.path)
    node = Node.get_local()
    role = node.role if node else None
    landing_filters = Q()
    if role:
        landing_filters |= Q(node_role=role)
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        landing_filters |= Q(user=user)
        user_group_ids = list(user.groups.values_list("pk", flat=True))
        if user_group_ids:
            security_group_ids = list(
                SecurityGroup.objects.filter(pk__in=user_group_ids).values_list(
                    "pk", flat=True
                )
            )
            if security_group_ids:
                landing_filters |= Q(security_group_id__in=security_group_ids)
    if landing_filters:
        role_landing = (
            RoleLanding.objects.filter(
                landing_filters,
                is_deleted=False,
                landing__enabled=True,
                landing__is_deleted=False,
            )
            .select_related("landing")
            .order_by("-priority", "-pk")
            .first()
        )
        if role_landing and role_landing.landing_id:
            landing_obj = role_landing.landing
            target_path = landing_obj.path
            if target_path and target_path != request.path:
                return redirect(target_path)
    return _render_readme(request, role, force_footer=True)


@never_cache
def developer_article_detail(request, slug):
    """Render a published developer article with markdown formatting."""

    article = get_object_or_404(DeveloperArticle.objects.published(), slug=slug)
    html, toc_html = _render_markdown_with_toc(article.content)
    context = {
        "article": article,
        "title": article.title,
        "summary": article.summary,
        "content": html,
        "toc": toc_html,
        "page_url": request.build_absolute_uri(),
    }
    response = render(request, "pages/developer_article.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@never_cache
def readme(request, doc=None):
    node = Node.get_local()
    role = node.role if node else None
    return _render_readme(request, role, doc)


def sitemap(request):
    site = get_site(request)
    node = Node.get_local()
    role = node.role if node else None
    applications = Module.objects.filter(node_role=role)
    base = request.build_absolute_uri("/").rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    seen = set()
    for app in applications:
        loc = f"{base}{app.path}"
        if loc not in seen:
            seen.add(loc)
            lines.append(f"  <url><loc>{loc}</loc></url>")
    lines.append("</urlset>")
    return HttpResponse("\n".join(lines), content_type="application/xml")


@landing("Package Releases")
@security_group_required("Release Managers")
def release_admin_redirect(request):
    return redirect("admin:core_packagerelease_changelist")


def release_checklist(request):
    file_path = Path(settings.BASE_DIR) / "releases" / "release-checklist.md"
    if not file_path.exists():
        raise Http404("Release checklist not found")
    text = file_path.read_text(encoding="utf-8")
    html, toc_html = _render_markdown_with_toc(text)
    context = {"content": html, "title": "Release Checklist", "toc": toc_html}
    response = render(request, "pages/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response



@landing(_("Changelog"))
def changelog_report(request):
    try:
        initial_page = changelog.get_initial_page()
    except changelog.ChangelogError as exc:
        initial_sections = tuple()
        has_more = False
        next_page = None
        error_message = str(exc)
    else:
        initial_sections = initial_page.sections
        has_more = initial_page.has_more
        next_page = initial_page.next_page
        error_message = ""

    context = {
        "title": _("Changelog"),
        "initial_sections": initial_sections,
        "has_more_sections": has_more,
        "next_page": next_page,
        "initial_section_count": len(initial_sections),
        "error_message": error_message,
        "loading_label": _("Loading more updates…"),
        "error_label": _("Unable to load additional updates."),
        "complete_label": _("You're all caught up."),
    }
    response = render(request, "pages/changelog.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


def changelog_report_data(request):
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"error": _("Invalid page number.")}, status=400)

    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        return JsonResponse({"error": _("Invalid offset.")}, status=400)

    try:
        page_data = changelog.get_page(page_number, per_page=1, offset=offset)
    except changelog.ChangelogError as exc:
        return JsonResponse({"error": str(exc)}, status=503)

    if not page_data.sections:
        return JsonResponse({"html": "", "has_more": False, "next_page": None})

    html = loader.render_to_string(
        "includes/changelog/section_list.html",
        {"sections": page_data.sections, "variant": "public"},
        request=request,
    )
    return JsonResponse(
        {"html": html, "has_more": page_data.has_more, "next_page": page_data.next_page}
    )


class CustomLoginView(LoginView):
    """Login view that redirects staff to the admin."""

    template_name = "pages/login.html"
    form_class = AuthenticatorLoginForm

    def dispatch(self, request, *args, **kwargs):
        allow_check = request.user.is_authenticated and (
            "check" in request.GET or "check" in request.POST
        )
        self._login_check_mode = allow_check
        if request.user.is_authenticated and not allow_check:
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if getattr(self, "_login_check_mode", False):
            username = self.request.user.get_username()
            if username:
                form.fields["username"].initial = username
            form.fields["username"].widget.attrs.setdefault("readonly", "readonly")
            form.fields["username"].widget.attrs.setdefault("aria-readonly", "true")
        return form

    def get_initial(self):
        initial = super().get_initial()
        if getattr(self, "_login_check_mode", False):
            initial.setdefault("username", self.request.user.get_username())
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_site = get_site(self.request)
        redirect_target = self.request.GET.get(self.redirect_field_name)
        restricted_notice = None
        if redirect_target:
            parsed_target = urlparse(redirect_target)
            target_path = parsed_target.path or redirect_target
            try:
                simulator_path = reverse("cp-simulator")
            except NoReverseMatch:  # pragma: no cover - simulator may be uninstalled
                simulator_path = None
            if simulator_path and target_path.startswith(simulator_path):
                restricted_notice = _(
                    "This page is reserved for members only. Please log in to continue."
                )
        redirect_value = context.get(self.redirect_field_name) or self.get_success_url()
        context[self.redirect_field_name] = redirect_value
        context["next"] = redirect_value
        context.update(
            {
                "site": current_site,
                "site_name": getattr(current_site, "name", ""),
                "can_request_invite": mailer.can_send_email(),
                "restricted_notice": restricted_notice,
                "login_check_mode": getattr(self, "_login_check_mode", False),
                "username_readonly": getattr(self, "_login_check_mode", False),
            }
        )
        context["authenticator_check_url"] = reverse("pages:authenticator-login-check")
        passkey_login_enabled = getattr(settings, "PASSKEY_LOGIN_ENABLED", False)
        context["passkey_login_enabled"] = passkey_login_enabled
        if passkey_login_enabled:
            context["passkey_login_options_url"] = reverse("pages:passkey-login-options")
            context["passkey_login_verify_url"] = reverse("pages:passkey-login-verify")
        node = Node.get_local()
        has_rfid_scanner = False
        had_rfid_feature = False
        if node:
            had_rfid_feature = node.has_feature("rfid-scanner")
            try:
                node.refresh_features()
            except Exception:
                logger.exception("Unable to refresh node features for login page")
            has_rfid_scanner = node.has_feature("rfid-scanner") or had_rfid_feature
        context["show_rfid_login"] = has_rfid_scanner
        if has_rfid_scanner:
            context["rfid_login_url"] = reverse("pages:rfid-login")
        return context

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url
        if self.request.user.is_staff:
            return reverse("admin:index")
        return "/"

    def form_valid(self, form):
        response = super().form_valid(form)
        device = form.get_verified_device()
        if device is not None:
            if device.user_id != self.request.user.pk:
                device.user = self.request.user
                device.user_id = self.request.user.pk
            self.request.session[DEVICE_ID_SESSION_KEY] = device.persistent_id
            self.request.user.otp_device = device
            otp_login(self.request, device)
        return response


login_view = CustomLoginView.as_view()


@require_POST
@csrf_protect
def authenticator_login_check(request):
    """Return whether an authenticator login requires a password."""

    username = (request.POST.get("username") or "").strip()
    if not username:
        return JsonResponse(
            {"error": _("Enter your username to continue with the authenticator.")},
            status=400,
        )

    UserModel = get_user_model()
    try:
        user = UserModel._default_manager.get_by_natural_key(username)
    except UserModel.DoesNotExist:
        return JsonResponse(
            {
                "error": _(
                    "No authenticator enrollment was found for the provided username."
                )
            },
            status=404,
        )

    devices = list(get_user_totp_devices(user))
    if not devices:
        return JsonResponse(
            {
                "error": _(
                    "No authenticator enrollment was found for the provided username."
                )
            },
            status=404,
        )

    enforce_password = bool(getattr(user, "require_2fa", False))
    allows_passwordless = totp_devices_allow_passwordless(devices)
    requires_password = enforce_password or totp_devices_require_password(
        devices, enforce=enforce_password
    )
    password_optional = requires_password and allows_passwordless and not enforce_password
    return JsonResponse(
        {
            "requires_password": requires_password,
            "password_optional": password_optional,
            "username": user.get_username(),
        }
    )


def _get_login_redirect(request, user, candidate: str | None = None) -> str:
    """Return the appropriate redirect after a passkey login."""

    redirect_field_name = CustomLoginView.redirect_field_name
    redirect_target = candidate or ""
    if redirect_target and not url_has_allowed_host_and_scheme(
        redirect_target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_target = ""
    if redirect_target:
        return redirect_target
    if user.is_staff:
        return reverse("admin:index")
    return "/"


@require_POST
@ensure_csrf_cookie
def passkey_login_options(request):
    """Return assertion options for a passkey login attempt."""

    options = passkeys.build_authentication_options(request)
    request.session[PASSKEY_LOGIN_SESSION_KEY] = options.challenge
    request.session.modified = True
    return JsonResponse({"publicKey": options.data})


@require_POST
def passkey_login_verify(request):
    """Verify a passkey assertion and log the user in."""

    challenge = request.session.pop(PASSKEY_LOGIN_SESSION_KEY, None)
    if not challenge:
        return JsonResponse({"error": "missing-challenge"}, status=400)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid-json"}, status=400)

    credential_data = payload.get("credential") or payload
    if not isinstance(credential_data, dict):
        return JsonResponse({"error": "invalid-credential"}, status=400)

    credential_id = credential_data.get("id")
    if not credential_id:
        return JsonResponse({"error": "missing-id"}, status=400)

    try:
        passkey = PasskeyCredential.objects.select_related("user").get(
            credential_id=credential_id
        )
    except PasskeyCredential.DoesNotExist:
        return JsonResponse({"error": "unknown-credential"}, status=400)

    response_data = credential_data.get("response")
    if isinstance(response_data, dict):
        response_handle = response_data.get("userHandle")
        if response_handle and response_handle != passkey.user_handle:
            return JsonResponse({"error": "mismatched-user"}, status=400)

    try:
        verification = passkeys.verify_authentication_response(
            request,
            credential=credential_data,
            expected_challenge=challenge,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
        )
    except Exception:  # pragma: no cover - detailed errors are not exposed to clients
        logger.exception("Passkey authentication verification failed")
        return JsonResponse({"error": "verification-failed"}, status=400)

    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at", "updated_at"])

    user = passkey.user
    login(request, user, backend="apps.core.backends.PasskeyBackend")

    redirect_target = payload.get(CustomLoginView.redirect_field_name)
    return JsonResponse({"redirect": _get_login_redirect(request, user, redirect_target)})


@ensure_csrf_cookie
def rfid_login_page(request):
    node = Node.get_local()
    if not node or not node.has_feature("rfid-scanner"):
        raise Http404
    if request.user.is_authenticated:
        return redirect(reverse("admin:index") if request.user.is_staff else "/")
    redirect_field_name = CustomLoginView.redirect_field_name
    redirect_target = request.GET.get(redirect_field_name, "")
    if redirect_target and not url_has_allowed_host_and_scheme(
        redirect_target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_target = ""
    context = {
        "login_api_url": reverse("rfid-login"),
        "scan_api_url": reverse("rfid-scan-next"),
        "redirect_field_name": redirect_field_name,
        "redirect_target": redirect_target,
        "back_url": reverse("pages:login"),
    }
    return render(request, "pages/rfid_login.html", context)


@staff_member_required
def authenticator_setup(request):
    """Allow staff to enroll an authenticator app for TOTP logins."""

    user = request.user
    device_qs = TOTPDevice.objects.filter(user=user)
    if TOTP_DEVICE_NAME:
        device_qs = device_qs.filter(name=TOTP_DEVICE_NAME)

    pending_device = device_qs.filter(confirmed=False).order_by("-id").first()
    confirmed_device = device_qs.filter(confirmed=True).order_by("-id").first()
    enrollment_form = AuthenticatorEnrollmentForm(device=pending_device)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate":
            device = pending_device or confirmed_device or TOTPDevice(user=user)
            if TOTP_DEVICE_NAME:
                device.name = TOTP_DEVICE_NAME
            if device.pk is None:
                device.save()
            device.key = TOTPDevice._meta.get_field("key").get_default()
            device.confirmed = False
            device.drift = 0
            device.last_t = -1
            device.throttling_failure_count = 0
            device.throttling_failure_timestamp = None
            device.throttle_reset(commit=False)
            device.save()
            messages.success(
                request,
                _(
                    "Scan the QR code with your authenticator app, then "
                    "enter a code below to confirm enrollment."
                ),
            )
            return redirect("pages:authenticator-setup")
        if action == "confirm" and pending_device is not None:
            enrollment_form = AuthenticatorEnrollmentForm(
                request.POST, device=pending_device
            )
            if enrollment_form.is_valid():
                pending_device.confirmed = True
                pending_device.save(update_fields=["confirmed"])
                messages.success(
                    request,
                    _(
                        "Authenticator app confirmed. You can now log in "
                        "with codes from your device."
                    ),
                )
                return redirect("pages:authenticator-setup")
        if action == "remove":
            if device_qs.exists():
                device_qs.delete()
                messages.success(
                    request,
                    _(
                        "Authenticator enrollment removed. Password logins "
                        "remain available."
                    ),
                )
            return redirect("pages:authenticator-setup")

    pending_device = device_qs.filter(confirmed=False).order_by("-id").first()
    confirmed_device = device_qs.filter(confirmed=True).order_by("-id").first()

    qr_data_uri = None
    manual_key = None
    if pending_device is not None:
        config_url = pending_device.config_url
        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(config_url)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        qr_data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
            "ascii"
        )
        secret = pending_device.key or ""
        manual_key = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))

    context = {
        "pending_device": pending_device,
        "confirmed_device": confirmed_device,
        "qr_data_uri": qr_data_uri,
        "manual_key": manual_key,
        "enrollment_form": enrollment_form,
    }
    passkey_qs = PasskeyCredential.objects.filter(user=user).order_by("name", "created_at")
    context.update(
        {
            "passkeys": passkey_qs,
            "passkey_register_options_url": reverse("pages:passkey-register-options"),
            "passkey_register_verify_url": reverse("pages:passkey-register-verify"),
            "passkey_name_max_length": PASSKEY_NAME_MAX_LENGTH,
        }
    )
    return TemplateResponse(request, "pages/authenticator_setup.html", context)


@staff_member_required
@require_POST
def passkey_register_options(request):
    """Start a new passkey registration flow for the current user."""

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid-json"}, status=400)

    name = (payload.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "name-required"}, status=400)
    if len(name) > PASSKEY_NAME_MAX_LENGTH:
        return JsonResponse({"error": "name-too-long", "max_length": PASSKEY_NAME_MAX_LENGTH}, status=400)

    user = request.user
    if PasskeyCredential.objects.filter(user=user, name=name).exists():
        return JsonResponse({"error": "duplicate-name"}, status=400)

    site = get_site(request)
    rp_name = getattr(site, "name", "") or "Arthexis"
    user_name = user.get_username() or user.email or str(user.pk)
    display_name = (user.get_full_name() or user_name).strip() or user_name
    user_id = bytes_to_base64url(str(user.pk).encode("utf-8"))

    exclude = [
        base64url_to_bytes(entry.credential_id)
        for entry in PasskeyCredential.objects.filter(user=user)
    ]

    options = passkeys.build_registration_options(
        request,
        user_id=user_id,
        user_name=user_name,
        user_display_name=display_name,
        rp_name=rp_name,
        exclude_credentials=exclude,
    )

    request.session[PASSKEY_REGISTRATION_SESSION_KEY] = {
        "challenge": options.challenge,
        "user_handle": options.user_handle,
        "name": name,
        "user_id": user_id,
    }
    request.session.modified = True

    return JsonResponse({"publicKey": options.data})


@staff_member_required
@require_POST
def passkey_register_verify(request):
    """Complete a pending passkey registration."""

    session_data = request.session.get(PASSKEY_REGISTRATION_SESSION_KEY)
    if not session_data:
        return JsonResponse({"error": "missing-challenge"}, status=400)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid-json"}, status=400)

    credential_data = payload.get("credential") or payload
    if not isinstance(credential_data, dict):
        return JsonResponse({"error": "invalid-credential"}, status=400)

    try:
        verification = passkeys.verify_registration_response(
            request,
            credential=credential_data,
            expected_challenge=session_data.get("challenge", ""),
        )
    except Exception:  # pragma: no cover - sensitive failure details remain server-side
        logger.exception("Passkey registration verification failed")
        request.session.pop(PASSKEY_REGISTRATION_SESSION_KEY, None)
        return JsonResponse({"error": "verification-failed"}, status=400)

    request.session.pop(PASSKEY_REGISTRATION_SESSION_KEY, None)

    credential_id = bytes_to_base64url(verification.credential_id)
    if PasskeyCredential.objects.filter(credential_id=credential_id).exists():
        return JsonResponse({"error": "duplicate-credential"}, status=400)

    response_data = credential_data.get("response")
    if isinstance(response_data, dict):
        response_handle = response_data.get("userHandle")
        expected_handle = session_data.get("user_handle")
        if response_handle and expected_handle and response_handle != expected_handle:
            return JsonResponse({"error": "mismatched-user"}, status=400)

    transports = payload.get("transports")
    if transports is None and isinstance(credential_data, dict):
        transports = credential_data.get("transports")
    if not isinstance(transports, (list, tuple)):
        transports = []
    transports = [str(value) for value in transports if isinstance(value, str)]

    passkey = PasskeyCredential.objects.create(
        user=request.user,
        name=session_data.get("name", ""),
        credential_id=credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        user_handle=session_data.get("user_handle", session_data.get("user_id", "")),
        transports=transports,
    )

    return JsonResponse(
        {
            "id": passkey.pk,
            "name": passkey.name,
            "created_at": timezone.localtime(passkey.created_at).isoformat(),
            "delete_url": reverse("pages:passkey-delete", args=[passkey.pk]),
        }
    )


@staff_member_required
@require_POST
def passkey_delete(request, pk: int):
    """Remove a stored passkey for the current user."""

    passkey = get_object_or_404(PasskeyCredential, pk=pk, user=request.user)
    passkey.delete()
    return JsonResponse({"deleted": True})


INVITATION_REQUEST_MIN_SUBMISSION_INTERVAL = datetime.timedelta(seconds=3)
INVITATION_REQUEST_THROTTLE_LIMIT = 3
INVITATION_REQUEST_THROTTLE_WINDOW = datetime.timedelta(hours=1)
INVITATION_REQUEST_HONEYPOT_MESSAGE = _(
    "We could not process your request. Please try again."
)
INVITATION_REQUEST_TOO_FAST_MESSAGE = _(
    "That was a little too fast. Please wait a moment and try again."
)
INVITATION_REQUEST_TIMESTAMP_ERROR = _(
    "We could not verify your submission. Please reload the page and try again."
)
INVITATION_REQUEST_THROTTLE_MESSAGE = _(
    "We've already received a few requests. Please try again later."
)


class _InvitationTemplateResponse(TemplateResponse):
    """Template response that always exposes its context."""

    @property
    def context(self):  # pragma: no cover - exercised by integration tests
        explicit = getattr(self, "_explicit_context", None)
        if explicit is not None:
            return explicit
        return getattr(self, "context_data", None)

    @context.setter
    def context(self, value):  # pragma: no cover - exercised by integration tests
        self._explicit_context = value


class InvitationRequestForm(forms.Form):
    email = forms.EmailField()
    comment = forms.CharField(
        required=False, widget=forms.Textarea, label=_("Comment")
    )
    honeypot = forms.CharField(
        required=False,
        label=_("Leave blank"),
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    timestamp = forms.DateTimeField(required=False, widget=forms.HiddenInput())

    min_submission_interval = INVITATION_REQUEST_MIN_SUBMISSION_INTERVAL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["timestamp"].initial = timezone.now()
        self.fields["honeypot"].widget.attrs.setdefault("aria-hidden", "true")
        self.fields["honeypot"].widget.attrs.setdefault("tabindex", "-1")

    def clean(self):
        cleaned = super().clean()

        honeypot_value = cleaned.get("honeypot", "")
        if honeypot_value:
            raise forms.ValidationError(INVITATION_REQUEST_HONEYPOT_MESSAGE)

        timestamp = cleaned.get("timestamp")
        if timestamp is None:
            cleaned["timestamp"] = timezone.now()
            return cleaned

        now = timezone.now()
        if timestamp > now or (now - timestamp) < self.min_submission_interval:
            raise forms.ValidationError(INVITATION_REQUEST_TOO_FAST_MESSAGE)

        return cleaned


@csrf_exempt
@ensure_csrf_cookie
def request_invite(request):
    form = InvitationRequestForm(request.POST if request.method == "POST" else None)
    sent = False
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        comment = form.cleaned_data.get("comment", "")
        ip_address = request.META.get("REMOTE_ADDR")
        throttle_filters = Q(email__iexact=email)
        if ip_address:
            throttle_filters |= Q(ip_address=ip_address)
        window_start = timezone.now() - INVITATION_REQUEST_THROTTLE_WINDOW
        recent_requests = InviteLead.objects.filter(
            throttle_filters, created_on__gte=window_start
        )
        if recent_requests.count() >= INVITATION_REQUEST_THROTTLE_LIMIT:
            form.add_error(None, INVITATION_REQUEST_THROTTLE_MESSAGE)
        else:
            mac_address = public_wifi.resolve_mac_address(ip_address)
            lead = InviteLead.objects.create(
                email=email,
                comment=comment,
                user=request.user if request.user.is_authenticated else None,
                path=request.path,
                referer=get_original_referer(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                ip_address=ip_address,
                mac_address=mac_address or "",
            )
            logger.info("Invitation requested for %s", email)
            User = get_user_model()
            users = list(User.objects.filter(email__iexact=email))
            if not users:
                logger.warning("Invitation requested for unknown email %s", email)
            for user in users:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                link = request.build_absolute_uri(
                    reverse("pages:invitation-login", args=[uid, token])
                )
                subject = _("Your invitation link")
                body = _("Use the following link to access your account: %(link)s") % {
                    "link": link
                }
                try:
                    node = Node.get_local()
                    result = mailer.send(
                        subject,
                        body,
                        [email],
                        user=request.user if request.user.is_authenticated else None,
                        node=node,
                    )
                    lead.sent_via_outbox = getattr(result, "outbox", None)
                    lead.sent_on = timezone.now()
                    lead.error = ""
                    logger.info(
                        "Invitation email sent to %s (user %s): %s", email, user.pk, result
                    )
                except Exception as exc:
                    lead.error = f"{exc}. Ensure the email service is reachable and settings are correct."
                    lead.sent_via_outbox = None
                    logger.exception("Failed to send invitation email to %s", email)
            if lead.sent_on or lead.error:
                lead.save(update_fields=["sent_on", "error", "sent_via_outbox"])
            sent = True

    context = {"form": form, "sent": sent}
    response = _InvitationTemplateResponse(
        request, "pages/request_invite.html", context
    )
    # Expose the rendering context directly for callers that do not use Django's
    # template test instrumentation and would otherwise see ``None`` when
    # accessing ``response.context``.
    response.context_data = context
    response.context = context
    return response


class InvitationPasswordForm(forms.Form):
    new_password1 = forms.CharField(
        widget=forms.PasswordInput, required=False, label=_("New password")
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput, required=False, label=_("Confirm password")
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 or p2:
            if not p1 or not p2 or p1 != p2:
                raise forms.ValidationError(_("Passwords do not match"))
        return cleaned


def invitation_login(request, uidb64, token):
    User = get_user_model()
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except Exception:
        user = None
    if user is None or not default_token_generator.check_token(user, token):
        return HttpResponse(_("Invalid invitation link"), status=400)
    form = InvitationPasswordForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data.get("new_password1")
        if password:
            user.set_password(password)
        user.is_active = True
        user.save()
        node = Node.get_local()
        if node and node.has_feature("ap-router"):
            mac_address = public_wifi.resolve_mac_address(
                request.META.get("REMOTE_ADDR")
            )
            if not mac_address:
                mac_address = (
                    InviteLead.objects.filter(email__iexact=user.email)
                    .exclude(mac_address="")
                    .order_by("-created_on")
                    .values_list("mac_address", flat=True)
                    .first()
                )
            if mac_address:
                public_wifi.grant_public_access(user, mac_address)
        login(request, user, backend="apps.core.backends.LocalhostAdminBackend")
        return redirect(reverse("admin:index") if user.is_staff else "/")
    return render(request, "pages/invitation_login.html", {"form": form})


class ClientReportForm(forms.Form):
    PERIOD_CHOICES = [
        ("range", _("Date range")),
        ("week", _("Week")),
        ("month", _("Month")),
    ]
    RECURRENCE_CHOICES = ClientReportSchedule.PERIODICITY_CHOICES
    VIEW_CHOICES = [
        ("expanded", _("Expanded view")),
        ("summary", _("Summarized view")),
    ]
    period = forms.ChoiceField(
        choices=PERIOD_CHOICES,
        widget=forms.RadioSelect,
        initial="range",
        help_text=_("Choose how the reporting window will be calculated."),
    )
    start = forms.DateField(
        label=_("Start date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("First day included when using a custom date range."),
    )
    end = forms.DateField(
        label=_("End date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("Last day included when using a custom date range."),
    )
    week = forms.CharField(
        label=_("Week"),
        required=False,
        widget=forms.TextInput(attrs={"type": "week"}),
        help_text=_("Generates the report for the ISO week that you select."),
    )
    month = forms.DateField(
        label=_("Month"),
        required=False,
        widget=forms.DateInput(attrs={"type": "month"}),
        input_formats=["%Y-%m"],
        help_text=_("Generates the report for the calendar month that you select."),
    )
    view_mode = forms.ChoiceField(
        label=_("Report layout"),
        choices=VIEW_CHOICES,
        initial="expanded",
        widget=forms.RadioSelect,
        help_text=_(
            "Choose between detailed charge point sections or a combined summary table."
        ),
    )
    language = forms.ChoiceField(
        label=_("Report language"),
        choices=settings.LANGUAGES,
        help_text=_("Choose the language used for the generated report."),
    )
    title = forms.CharField(
        label=_("Report title"),
        required=False,
        max_length=200,
        help_text=_("Optional heading that replaces the default report title."),
    )
    chargers = forms.ModelMultipleChoiceField(
        label=_("Charge points"),
        queryset=Charger.objects.filter(connector_id__isnull=True)
        .order_by("display_name", "charger_id"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Choose which charge points are included in the report."),
    )
    owner = forms.ModelChoiceField(
        queryset=get_user_model().objects.all(),
        required=False,
        help_text=_(
            "Sets who owns the report schedule and is listed as the requester."
        ),
    )
    destinations = forms.CharField(
        label=_("Email destinations"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Separate addresses with commas, whitespace, or new lines."),
    )
    recurrence = forms.ChoiceField(
        label=_("Recurrence"),
        choices=RECURRENCE_CHOICES,
        initial=ClientReportSchedule.PERIODICITY_NONE,
        help_text=_("Defines how often the report should be generated automatically."),
    )
    enable_emails = forms.BooleanField(
        label=_("Enable email delivery"),
        required=False,
        help_text=_("Send the report via email to the recipients listed above."),
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            self.fields["owner"].initial = request.user.pk
        self.fields["chargers"].widget.attrs["class"] = "charger-options"
        if not self.is_bound:
            queryset = self.fields["chargers"].queryset
            self.fields["chargers"].initial = list(
                queryset.values_list("pk", flat=True)
            )
        language_initial = ClientReport.default_language()
        if request:
            language_initial = ClientReport.normalize_language(
                getattr(request, "LANGUAGE_CODE", language_initial)
            )
        self.fields["language"].initial = language_initial

    def clean(self):
        cleaned = super().clean()
        period = cleaned.get("period")
        if period == "range":
            if not cleaned.get("start") or not cleaned.get("end"):
                raise forms.ValidationError(_("Please provide start and end dates."))
        elif period == "week":
            week_str = cleaned.get("week")
            if not week_str:
                raise forms.ValidationError(_("Please select a week."))
            try:
                year_str, week_num_str = week_str.split("-W", 1)
                start = datetime.date.fromisocalendar(
                    int(year_str), int(week_num_str), 1
                )
            except (TypeError, ValueError):
                raise forms.ValidationError(_("Please select a week."))
            cleaned["start"] = start
            cleaned["end"] = start + datetime.timedelta(days=6)
        elif period == "month":
            month_dt = cleaned.get("month")
            if not month_dt:
                raise forms.ValidationError(_("Please select a month."))
            start = month_dt.replace(day=1)
            last_day = calendar.monthrange(month_dt.year, month_dt.month)[1]
            cleaned["start"] = start
            cleaned["end"] = month_dt.replace(day=last_day)
        return cleaned

    def clean_destinations(self):
        raw = self.cleaned_data.get("destinations", "")
        if not raw:
            return []
        validator = EmailValidator()
        seen: set[str] = set()
        emails: list[str] = []
        for part in re.split(r"[\s,]+", raw):
            candidate = part.strip()
            if not candidate:
                continue
            validator(candidate)
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            emails.append(candidate)
        return emails

    def clean_title(self):
        title = self.cleaned_data.get("title")
        return ClientReport.normalize_title(title)


@live_update()
def client_report(request):
    form = ClientReportForm(request.POST or None, request=request)
    report = None
    schedule = None
    report_rows = None
    report_summary_rows: list[dict[str, Any]] = []
    if request.method == "POST":
        post_result = _process_client_report_post(request, form)
        if post_result.response is not None:
            return post_result.response
        report = post_result.report
        schedule = post_result.schedule
        report_rows = post_result.report_rows
        report_summary_rows = post_result.report_summary_rows
    download_url = None
    download_param = request.GET.get("download")
    if download_param:
        try:
            download_id = int(download_param)
        except (TypeError, ValueError):
            download_id = None
        if download_id and request.user.is_authenticated:
            download_url = reverse(
                "pages:client-report-download", args=[download_id]
            )
        if download_url:
            setattr(request, "live_update_interval", None)

    try:
        login_url = reverse("pages:login")
    except NoReverseMatch:
        try:
            login_url = reverse("login")
        except NoReverseMatch:
            login_url = getattr(settings, "LOGIN_URL", None)

    if report and report_rows is None:
        report_rows = report.rows_for_display
        report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)

    selected_view_mode = form.fields["view_mode"].initial
    if form.is_bound:
        if form.is_valid():
            selected_view_mode = form.cleaned_data.get("view_mode", selected_view_mode)
        else:
            selected_view_mode = form.data.get("view_mode", selected_view_mode)

    context = {
        "form": form,
        "report": report,
        "schedule": schedule,
        "login_url": login_url,
        "download_url": download_url,
        "report_rows": report_rows,
        "report_summary_rows": report_summary_rows,
        "report_view_mode": selected_view_mode,
    }
    return render(request, "pages/client_report.html", context)


@dataclass
class _ClientReportPostResult:
    report: ClientReport | None = None
    schedule: ClientReportSchedule | None = None
    report_rows: list[dict[str, Any]] | None = None
    report_summary_rows: list[dict[str, Any]] = field(default_factory=list)
    response: HttpResponse | None = None


def _process_client_report_post(
    request, form: "ClientReportForm"
) -> _ClientReportPostResult:
    if not request.user.is_authenticated:
        # Run validation to surface field errors alongside auth error.
        form.is_valid()
        form.add_error(None, _("You must log in to generate consumer reports."))
        return _ClientReportPostResult()

    if not form.is_valid():
        return _ClientReportPostResult()

    throttle_error = _enforce_client_report_throttle(request)
    if throttle_error:
        form.add_error(None, throttle_error)
        return _ClientReportPostResult()

    return _generate_client_report_response(request, form)


def _enforce_client_report_throttle(request) -> str | None:
    throttle_seconds = getattr(settings, "CLIENT_REPORT_THROTTLE_SECONDS", 60)
    if not throttle_seconds:
        return None

    throttle_keys = _build_client_report_throttle_keys(request)
    added_keys: list[str] = []
    for key in throttle_keys:
        if cache.add(key, timezone.now(), throttle_seconds):
            added_keys.append(key)
            continue
        for added_key in added_keys:
            cache.delete(added_key)
        return _(
            "Consumer reports can only be generated periodically. Please wait before trying again."
        )
    return None


def _build_client_report_throttle_keys(request) -> list[str]:
    keys: list[str] = []
    if request.user.is_authenticated:
        keys.append(f"client-report:user:{request.user.pk}")

    remote_addr = request.META.get("HTTP_X_FORWARDED_FOR")
    if remote_addr:
        remote_addr = remote_addr.split(",")[0].strip()
    remote_addr = remote_addr or request.META.get("REMOTE_ADDR")
    if remote_addr:
        keys.append(f"client-report:ip:{remote_addr}")
    return keys


def _generate_client_report_response(
    request, form: "ClientReportForm"
) -> _ClientReportPostResult:
    owner = _resolve_client_report_owner(request, form)
    enable_emails = form.cleaned_data.get("enable_emails", False)
    disable_emails = not enable_emails
    recipients = form.cleaned_data.get("destinations") if enable_emails else []
    chargers = list(form.cleaned_data.get("chargers") or [])
    language = form.cleaned_data.get("language")
    title = form.cleaned_data.get("title")

    report = ClientReport.generate(
        form.cleaned_data["start"],
        form.cleaned_data["end"],
        owner=owner,
        recipients=recipients,
        disable_emails=disable_emails,
        chargers=chargers,
        language=language,
        title=title,
    )
    report.store_local_copy()
    if chargers:
        report.chargers.set(chargers)

    if enable_emails and recipients:
        _deliver_client_report_email(request, report, owner, recipients)

    schedule = _maybe_create_client_report_schedule(
        request,
        report,
        owner,
        form.cleaned_data.get("recurrence"),
        recipients,
        disable_emails,
        language,
        title,
        chargers,
    )

    if disable_emails:
        messages.success(
            request,
            _("Consumer report generated. The download will begin automatically."),
        )
        redirect_url = f"{reverse('pages:client-report')}?download={report.pk}"
        return _ClientReportPostResult(
            report=report,
            schedule=schedule,
            response=HttpResponseRedirect(redirect_url),
        )

    report_rows = report.rows_for_display
    report_summary_rows = ClientReport.build_evcs_summary_rows(report_rows)
    return _ClientReportPostResult(
        report=report,
        schedule=schedule,
        report_rows=report_rows,
        report_summary_rows=report_summary_rows,
    )


def _resolve_client_report_owner(request, form: "ClientReportForm"):
    owner = form.cleaned_data.get("owner")
    if not owner and request.user.is_authenticated:
        return request.user
    return owner


def _deliver_client_report_email(
    request,
    report: ClientReport,
    owner,
    recipients: list[str],
) -> None:
    delivered = report.send_delivery(
        to=recipients,
        cc=[],
        outbox=ClientReport.resolve_outbox_for_owner(owner),
        reply_to=ClientReport.resolve_reply_to_for_owner(owner),
    )
    if delivered:
        report.recipients = delivered
        report.save(update_fields=["recipients"])
        messages.success(
            request,
            _("Consumer report emailed to the selected recipients."),
        )


def _maybe_create_client_report_schedule(
    request,
    report: ClientReport,
    owner,
    recurrence,
    recipients,
    disable_emails: bool,
    language,
    title,
    chargers: list[Any],
) -> ClientReportSchedule | None:
    if not recurrence or recurrence == ClientReportSchedule.PERIODICITY_NONE:
        return None

    schedule = ClientReportSchedule.objects.create(
        owner=owner,
        created_by=request.user if request.user.is_authenticated else None,
        periodicity=recurrence,
        email_recipients=recipients,
        disable_emails=disable_emails,
        language=language,
        title=title,
    )
    if chargers:
        schedule.chargers.set(chargers)
    report.schedule = schedule
    report.save(update_fields=["schedule"])
    messages.success(
        request,
        _(
            "Consumer report schedule created; future reports will be generated automatically."
        ),
    )
    return schedule


@login_required
def client_report_download(request, report_id: int):
    report = get_object_or_404(ClientReport, pk=report_id)
    if not request.user.is_staff and report.owner_id != request.user.pk:
        return HttpResponseForbidden(
            _("You do not have permission to download this report.")
        )
    pdf_path = report.ensure_pdf()
    if not pdf_path.exists():
        raise Http404(_("Report file unavailable."))
    filename = f"consumer-report-{report.start_date}-{report.end_date}.pdf"
    response = FileResponse(pdf_path.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
@require_POST
def submit_user_story(request):
    throttle_seconds = getattr(settings, "USER_STORY_THROTTLE_SECONDS", 300)
    client_ip = _get_client_ip(request)
    cache_key = None

    if throttle_seconds:
        cache_key = f"user-story:ip:{client_ip or 'unknown'}"
        if not cache.add(cache_key, timezone.now(), throttle_seconds):
            minutes = throttle_seconds // 60
            if throttle_seconds % 60:
                minutes += 1
            error_message = _(
                "You can only submit feedback once every %(minutes)s minutes."
            ) % {"minutes": minutes or 1}
            return JsonResponse(
                {"success": False, "errors": {"__all__": [error_message]}},
                status=429,
            )

    data = request.POST.copy()
    anonymous_placeholder = ""
    if request.user.is_authenticated:
        data["name"] = request.user.get_username()[:40]
    elif not data.get("name"):
        anonymous_placeholder = "anonymous@example.invalid"
        data["name"] = anonymous_placeholder
    if not data.get("path"):
        data["path"] = request.get_full_path()

    form = UserStoryForm(data, user=request.user)
    if request.user.is_authenticated:
        form.instance.user = request.user

    if form.is_valid():
        story = form.save(commit=False)
        if anonymous_placeholder and story.name == anonymous_placeholder:
            story.name = ""
        if request.user.is_authenticated:
            story.user = request.user
            story.owner = request.user
            story.name = request.user.get_username()[:40]
        if not story.name:
            story.name = str(_("Anonymous"))[:40]
        story.path = (story.path or request.get_full_path())[:500]
        story.referer = get_original_referer(request)
        story.user_agent = request.META.get("HTTP_USER_AGENT", "")
        story.ip_address = client_ip or None
        story.is_user_data = True
        language_code = getattr(request, "selected_language_code", "")
        if not language_code:
            language_code = get_request_language_code(request)
        if language_code:
            story.language_code = language_code
        story.save()
        if story.take_screenshot:
            screenshot_url = request.META.get("HTTP_REFERER", "")
            parsed = urlparse(screenshot_url)
            if not (parsed.scheme and parsed.netloc):
                target_path = story.path or request.get_full_path() or "/"
                screenshot_url = request.build_absolute_uri(target_path)
            try:
                screenshot_path = capture_screenshot(screenshot_url)
            except Exception:  # pragma: no cover - best effort capture
                logger.exception("Failed to capture screenshot for user story %s", story.pk)
            else:
                try:
                    sample = save_screenshot(
                        screenshot_path,
                        method="USER_STORY",
                        user=story.user if story.user_id else None,
                        link_duplicates=True,
                    )
                except Exception:  # pragma: no cover - best effort persistence
                    logger.exception(
                        "Failed to persist screenshot for user story %s", story.pk
                    )
                else:
                    if sample is not None:
                        story.screenshot = sample
                        story.save(update_fields=["screenshot"])
        return JsonResponse({"success": True})

    return JsonResponse({"success": False, "errors": form.errors}, status=400)


def csrf_failure(request, reason=""):
    """Custom CSRF failure view with a friendly message."""
    logger.warning("CSRF failure on %s: %s", request.path, reason)
    return render(request, "pages/csrf_failure.html", status=403)


def _admin_context(request):
    context = admin.site.each_context(request)
    if not context.get("has_permission"):
        rf = RequestFactory()
        mock_request = rf.get(request.path)
        mock_request.user = SimpleNamespace(
            is_active=True,
            is_staff=True,
            is_superuser=True,
            has_perm=lambda perm, obj=None: True,
            has_module_perms=lambda app_label: True,
        )
        context["available_apps"] = admin.site.get_app_list(mock_request)
        context["has_permission"] = True
    return context


def _render_admin_template(
    request,
    template_name: str,
    extra_context: dict[str, Any] | None = None,
    *,
    status: int | None = None,
):
    context = _admin_context(request)
    if extra_context:
        context.update(extra_context)
    response = render(request, template_name, context, status=status)
    if getattr(response, "context", None) is None:
        response.context = context
    if test_signals.template_rendered.receivers:
        template = loader.get_template(template_name)
        signal_context = context
        if request is not None and "request" not in signal_context:
            signal_context = {**context, "request": request}
        test_signals.template_rendered.send(
            sender=template.__class__,
            template=template,
            context=signal_context,
        )
    return response


@staff_member_required
@never_cache
def admin_user_tools(request):
    return_url = request.META.get("HTTP_HX_CURRENT_URL", request.get_full_path())
    return _render_admin_template(
        request,
        "admin/includes/user_tools.html",
        {"user_tools_return_url": return_url},
    )


def admin_manual_list(request):
    manuals = UserManual.objects.order_by("title")
    return _render_admin_template(
        request,
        "admin_doc/manuals.html",
        {"manuals": manuals},
    )


def admin_manual_detail(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    return _render_admin_template(
        request,
        "admin_doc/manual_detail.html",
        {"manual": manual},
    )


# WhatsApp callbacks originate outside the site and cannot include CSRF tokens.
@csrf_exempt
def whatsapp_webhook(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not getattr(settings, "PAGES_WHATSAPP_ENABLED", False):
        return HttpResponse(status=503)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest(_("Invalid JSON payload."))

    from_number = (payload.get("from") or payload.get("from_number") or "").strip()
    text = (payload.get("message") or payload.get("text") or "").strip()
    if not from_number or not text:
        return HttpResponseBadRequest(
            _("Missing WhatsApp sender or message body."),
        )
    display_name = (payload.get("display_name") or from_number).strip()

    site_value = payload.get("site") or payload.get("site_domain")
    site = None
    if site_value:
        site = Site.objects.filter(Q(id=site_value) | Q(domain=site_value)).first()
    if site is None:
        try:
            site = Site.objects.get_current()
        except Exception:
            site = None

    session = (
        ChatSession.objects.filter(whatsapp_number=from_number)
        .order_by("-last_activity_at")
        .first()
    )
    if session is None:
        session = ChatSession.objects.create(
            site=site,
            visitor_key=f"whatsapp:{from_number}",
            whatsapp_number=from_number,
        )
    elif site and session.site_id is None:
        session.site = site
        session.save(update_fields=["site"])

    message = session.add_message(
        content=text,
        display_name=display_name,
        source="whatsapp",
    )
    response_payload = {"status": "ok", "session": str(session.uuid)}
    if getattr(message, "pk", None):
        response_payload["message"] = message.pk
    return JsonResponse(response_payload, status=201)


def manual_pdf(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    pdf_data = base64.b64decode(manual.content_pdf)
    response = HttpResponse(pdf_data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{manual.slug}.pdf"'
    return response


@landing(_("Manuals"))
def manual_list(request):
    manuals = UserManual.objects.order_by("title")
    return render(request, "pages/manual_list.html", {"manuals": manuals})


def manual_detail(request, slug):
    manual = get_object_or_404(UserManual, slug=slug)
    return render(request, "pages/manual_detail.html", {"manual": manual})
