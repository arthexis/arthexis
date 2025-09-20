import logging
from pathlib import Path
import datetime
import calendar
import shutil
import re
from html import escape

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model, login
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django import forms
from django.apps import apps as django_apps
from utils.sites import get_site
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from nodes.models import Node
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from core import mailer, public_wifi
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from django.utils.cache import patch_vary_headers
from django.utils.text import slugify
from django.core.validators import EmailValidator
from core.models import InviteLead, ClientReport, ClientReportSchedule

try:  # pragma: no cover - optional dependency guard
    from graphviz import Digraph
    from graphviz.backend import CalledProcessError, ExecutableNotFound
except ImportError:  # pragma: no cover - handled gracefully in views
    Digraph = None
    CalledProcessError = ExecutableNotFound = None

import markdown
from pages.utils import landing
from core.liveupdate import live_update
from .models import Module


logger = logging.getLogger(__name__)


def _get_registered_models(app_label: str):
    """Return admin-registered models for the given app label."""

    registered = [
        model for model in admin.site._registry if model._meta.app_label == app_label
    ]
    return sorted(registered, key=lambda model: str(model._meta.verbose_name))


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
        "admin_app_models",
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
        graph.node(node_id, label=label)

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
                graph.edge(source_id, node_ids[related], **attrs)

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
                graph.edge(source_id, node_ids[related], **attrs)

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

    context = admin.site.each_context(request)
    context.update(
        {
            "app_label": app_label,
            "app_verbose_name": app_config.verbose_name,
            "graph_source": graph_source,
            "graph_svg": graph_svg,
            "graph_error": graph_error,
            "models": model_links,
            "title": _("%(app)s model graph") % {"app": app_config.verbose_name},
            "download_url": download_url,
        }
    )

    return TemplateResponse(request, "admin/model_graph.html", context)


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
    app = (
        Module.objects.filter(node_role=role, is_default=True)
        .select_related("application")
        .first()
    )
    app_slug = app.path.strip("/") if app else ""
    readme_base = (
        Path(settings.BASE_DIR) / app_slug if app_slug else Path(settings.BASE_DIR)
    )
    lang = getattr(request, "LANGUAGE_CODE", "")
    lang = lang.replace("_", "-").lower()
    root_base = Path(settings.BASE_DIR)
    candidates = []
    if lang:
        candidates.append(readme_base / f"README.{lang}.md")
        short = lang.split("-")[0]
        if short != lang:
            candidates.append(readme_base / f"README.{short}.md")
    candidates.append(readme_base / "README.md")
    if readme_base != root_base:
        if lang:
            candidates.append(root_base / f"README.{lang}.md")
            short = lang.split("-")[0]
            if short != lang:
                candidates.append(root_base / f"README.{short}.md")
        candidates.append(root_base / "README.md")
    readme_file = next((p for p in candidates if p.exists()), root_base / "README.md")
    text = readme_file.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["toc", "tables"])
    html = md.convert(text)
    toc_html = md.toc
    if toc_html.strip().startswith('<div class="toc">'):
        toc_html = toc_html.strip()[len('<div class="toc">') :]
        if toc_html.endswith("</div>"):
            toc_html = toc_html[: -len("</div>")]
        toc_html = toc_html.strip()
    title = "README" if readme_file.name.startswith("README") else readme_file.stem
    context = {"content": html, "title": title, "toc": toc_html}
    response = render(request, "pages/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


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


def release_checklist(request):
    file_path = Path(settings.BASE_DIR) / "releases" / "release-checklist.md"
    if not file_path.exists():
        raise Http404("Release checklist not found")
    text = file_path.read_text(encoding="utf-8")
    md = markdown.Markdown(extensions=["toc", "tables"])
    html = md.convert(text)
    toc_html = md.toc
    if toc_html.strip().startswith('<div class="toc">'):
        toc_html = toc_html.strip()[len('<div class="toc">') :]
        if toc_html.endswith("</div>"):
            toc_html = toc_html[: -len("</div>")]
        toc_html = toc_html.strip()
    context = {"content": html, "title": "Release Checklist", "toc": toc_html}
    response = render(request, "pages/readme.html", context)
    patch_vary_headers(response, ["Accept-Language", "Cookie"])
    return response


@csrf_exempt
def datasette_auth(request):
    if request.user.is_authenticated:
        return HttpResponse("OK")
    return HttpResponse(status=401)


class CustomLoginView(LoginView):
    """Login view that redirects staff to the admin."""

    template_name = "pages/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(LoginView, self).get_context_data(**kwargs)
        current_site = get_site(self.request)
        context.update(
            {
                "site": current_site,
                "site_name": getattr(current_site, "name", ""),
                "next": self.get_success_url(),
                "can_request_invite": mailer.can_send_email(),
            }
        )
        return context

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url
        if self.request.user.is_staff:
            return reverse("admin:index")
        return "/"


login_view = CustomLoginView.as_view()


class InvitationRequestForm(forms.Form):
    email = forms.EmailField()
    comment = forms.CharField(required=False, widget=forms.Textarea, label=_("Comment"))


@csrf_exempt
@ensure_csrf_cookie
def request_invite(request):
    form = InvitationRequestForm(request.POST if request.method == "POST" else None)
    sent = False
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        comment = form.cleaned_data.get("comment", "")
        ip_address = request.META.get("REMOTE_ADDR")
        mac_address = public_wifi.resolve_mac_address(ip_address)
        lead = InviteLead.objects.create(
            email=email,
            comment=comment,
            user=request.user if request.user.is_authenticated else None,
            path=request.path,
            referer=request.META.get("HTTP_REFERER", ""),
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
                node_error = None
                node = Node.get_local()
                if node:
                    try:
                        result = node.send_mail(subject, body, [email])
                    except Exception as exc:
                        node_error = exc
                        logger.exception(
                            "Node send_mail failed, falling back to default backend"
                        )
                        result = mailer.send(
                            subject, body, [email], settings.DEFAULT_FROM_EMAIL
                        )
                else:
                    result = mailer.send(
                        subject, body, [email], settings.DEFAULT_FROM_EMAIL
                    )
                lead.sent_on = timezone.now()
                if node_error:
                    lead.error = (
                        f"Node email send failed: {node_error}. "
                        "Invite was sent using default mail backend; ensure the "
                        "node's email service is running or check its configuration."
                    )
                else:
                    lead.error = ""
                logger.info(
                    "Invitation email sent to %s (user %s): %s", email, user.pk, result
                )
            except Exception as exc:
                lead.error = f"{exc}. Ensure the email service is reachable and settings are correct."
                logger.exception("Failed to send invitation email to %s", email)
        if lead.sent_on or lead.error:
            lead.save(update_fields=["sent_on", "error"])
        sent = True
    return render(request, "pages/request_invite.html", {"form": form, "sent": sent})


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
        if node and node.has_feature("ap-public-wifi"):
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
        login(request, user, backend="core.backends.LocalhostAdminBackend")
        return redirect(reverse("admin:index") if user.is_staff else "/")
    return render(request, "pages/invitation_login.html", {"form": form})


class ClientReportForm(forms.Form):
    PERIOD_CHOICES = [
        ("range", _("Date range")),
        ("week", _("Week")),
        ("month", _("Month")),
    ]
    RECURRENCE_CHOICES = ClientReportSchedule.PERIODICITY_CHOICES
    period = forms.ChoiceField(
        choices=PERIOD_CHOICES, widget=forms.RadioSelect, initial="range"
    )
    start = forms.DateField(
        label=_("Start date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end = forms.DateField(
        label=_("End date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    week = forms.CharField(
        label=_("Week"),
        required=False,
        widget=forms.TextInput(attrs={"type": "week"}),
    )
    month = forms.DateField(
        label=_("Month"),
        required=False,
        widget=forms.DateInput(attrs={"type": "month"}),
    )
    owner = forms.ModelChoiceField(
        queryset=get_user_model().objects.all(), required=False
    )
    destinations = forms.CharField(
        label=_("Email destinations"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Separate addresses with commas or new lines."),
    )
    recurrence = forms.ChoiceField(
        label=_("Recurrency"),
        choices=RECURRENCE_CHOICES,
        initial=ClientReportSchedule.PERIODICITY_NONE,
    )
    disable_emails = forms.BooleanField(
        label=_("Disable email delivery"),
        required=False,
        help_text=_("Generate files without sending emails."),
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            self.fields["owner"].initial = request.user.pk

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
            year, week_num = week_str.split("-W")
            start = datetime.date.fromisocalendar(int(year), int(week_num), 1)
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


@live_update()
def client_report(request):
    form = ClientReportForm(request.POST or None, request=request)
    report = None
    schedule = None
    if request.method == "POST" and form.is_valid():
        owner = form.cleaned_data.get("owner")
        if not owner and request.user.is_authenticated:
            owner = request.user
        report = ClientReport.generate(
            form.cleaned_data["start"],
            form.cleaned_data["end"],
            owner=owner,
            recipients=form.cleaned_data.get("destinations"),
            disable_emails=form.cleaned_data.get("disable_emails", False),
        )
        report.store_local_copy()
        recurrence = form.cleaned_data.get("recurrence")
        if recurrence and recurrence != ClientReportSchedule.PERIODICITY_NONE:
            schedule = ClientReportSchedule.objects.create(
                owner=owner,
                created_by=request.user if request.user.is_authenticated else None,
                periodicity=recurrence,
                email_recipients=form.cleaned_data.get("destinations", []),
                disable_emails=form.cleaned_data.get("disable_emails", False),
            )
            report.schedule = schedule
            report.save(update_fields=["schedule"])
            messages.success(
                request,
                _(
                    "Client report schedule created; future reports will be generated automatically."
                ),
            )
    context = {"form": form, "report": report, "schedule": schedule}
    return render(request, "pages/client_report.html", context)


def csrf_failure(request, reason=""):
    """Custom CSRF failure view with a friendly message."""
    logger.warning("CSRF failure on %s: %s", request.path, reason)
    return render(request, "pages/csrf_failure.html", status=403)
