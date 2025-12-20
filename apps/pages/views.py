import json
import logging
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
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django import forms
from django.apps import apps as django_apps
from apps.docs import views as docs_views
from apps.groups.decorators import security_group_required
from utils.sites import get_site
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
from apps.nodes.models import Node
from config.request_utils import is_https_request
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
from apps.core import changelog
from apps.emails import mailer
from apps.links.templatetags.ref_tags import build_footer_context
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from django.core.cache import cache
from django.views.decorators.cache import never_cache
from django.utils.cache import patch_vary_headers
from django.core.exceptions import PermissionDenied
from django.utils.text import slugify, Truncator
from django.core.validators import EmailValidator
from django.db.models import Q
from apps.energy.models import ClientReport, ClientReportSchedule
from apps.core.models import InviteLead
from apps.ocpp.models import Charger
from .utils import get_original_referer, get_request_language_code, landing


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

from .forms import AuthenticatorLoginForm, UserStoryForm
from apps.modules.models import Module
from .models import (
    UserStory,
)
from apps.chats.models import ChatSession


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




@landing("Home")
@never_cache
def index(request):
    site = get_site(request)
    if site:
        badge = getattr(site, "badge", None)
        landing = getattr(badge, "landing_override", None)
        if landing is None:
            landing = getattr(site, "default_landing", None)
        if landing and not getattr(landing, "is_deleted", False) and landing.enabled:
            target_path = landing.path
            if target_path and target_path != request.path:
                return redirect(target_path)
    node = Node.get_local()
    role = node.role if node else None
    return docs_views.render_readme_page(request, force_footer=True, role=role)


def sitemap(request):
    site = get_site(request)
    node = Node.get_local()
    role = node.role if node else None
    applications = Module.objects.for_role(role).filter(is_deleted=False)
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
def release_checklist(request):
    file_path = Path(settings.BASE_DIR) / "releases" / "release-checklist.md"
    if not file_path.exists():
        raise Http404("Release checklist not found")
    text = file_path.read_text(encoding="utf-8")
    html, toc_html = docs_views.render_markdown_with_toc(text)
    context = {"content": html, "title": "Release Checklist", "toc": toc_html}
    response = render(request, "docs/readme.html", context)
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
                simulator_path = reverse("ocpp:cp-simulator")
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
        return response


login_view = CustomLoginView.as_view()


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
        require_https=is_https_request(request),
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


def logout_view(request):
    """Log out the current user and redirect to a safe target."""

    redirect_target = request.GET.get(CustomLoginView.redirect_field_name, "")
    if redirect_target and not url_has_allowed_host_and_scheme(
        redirect_target,
        allowed_hosts={request.get_host()},
        require_https=is_https_request(request),
    ):
        redirect_target = ""

    logout(request)

    if redirect_target:
        return redirect(redirect_target)

    return redirect(reverse("pages:login"))


@staff_member_required
def authenticator_setup(request):
    raise Http404




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
            lead = InviteLead.objects.create(
                email=email,
                comment=comment,
                user=request.user if request.user.is_authenticated else None,
                path=request.path,
                referer=get_original_referer(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                ip_address=ip_address,
                mac_address="",
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
        login(request, user, backend="apps.users.backends.LocalhostAdminBackend")
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

