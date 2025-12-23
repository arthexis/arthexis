import datetime
import json
import logging
import shutil
from html import escape
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from django import forms
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django.contrib.sites.models import Site
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import redirect, render
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
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from apps.chats.models import ChatSession
from apps.core.models import InviteLead
from apps.emails import mailer
from apps.nodes.models import Node
from config.request_utils import is_https_request

from ..forms import AuthenticatorLoginForm
from ..utils import get_original_referer
from utils.sites import get_site

logger = logging.getLogger(__name__)


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


def _get_registered_models(app_label: str):
    """Return admin-registered models for the given app label."""

    registered = [
        model for model in admin.site._registry if model._meta.app_label == app_label
    ]
    return sorted(registered, key=lambda model: str(model._meta.verbose_name))


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
                        "Invitation email sent to %s (user %s): %s",
                        email,
                        user.pk,
                        result,
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
