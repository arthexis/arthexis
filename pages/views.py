import logging
from pathlib import Path
import datetime
import calendar

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django import forms
from utils.sites import get_site
from django.http import HttpResponse
from django.shortcuts import redirect, render
from nodes.models import Node
from django.urls import reverse
from django.utils import translation, timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.core.mail import send_mail
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from core.models import InviteLead, EnergyReport

import markdown
from pages.utils import landing
from .models import Module


logger = logging.getLogger(__name__)


@landing("Home")
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
    lang = translation.get_language() or ""
    readme_file = readme_base / "README.md"
    if lang:
        localized = readme_base / f"README.{lang}.md"
        if not localized.exists():
            short = lang.split("-")[0]
            localized = readme_base / f"README.{short}.md"
        if localized.exists():
            readme_file = localized
    if not readme_file.exists():
        readme_file = Path(settings.BASE_DIR) / "README.md"
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
    return render(request, "pages/readme.html", context)


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


class CustomLoginView(LoginView):
    """Login view that redirects staff to the admin."""

    template_name = "pages/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

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
        lead = InviteLead.objects.create(
            email=email,
            comment=comment,
            user=request.user if request.user.is_authenticated else None,
            path=request.path,
            referer=request.META.get("HTTP_REFERER", ""),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            ip_address=request.META.get("REMOTE_ADDR"),
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
                if node:
                    result = node.send_mail(subject, body, [email])
                else:
                    result = send_mail(
                        subject, body, settings.DEFAULT_FROM_EMAIL, [email]
                    )
                lead.sent_on = timezone.now()
                lead.error = ""
                logger.info(
                    "Invitation email sent to %s (user %s): %s", email, user.pk, result
                )
            except Exception as exc:
                lead.error = str(exc)
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
        login(request, user, backend="core.backends.LocalhostAdminBackend")
        return redirect(reverse("admin:index") if user.is_staff else "/")
    return render(request, "pages/invitation_login.html", {"form": form})


class EnergyReportForm(forms.Form):
    PERIOD_CHOICES = [
        ("range", _("Date range")),
        ("week", _("Week")),
        ("month", _("Month")),
    ]
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


def energy_report(request):
    form = EnergyReportForm(request.POST or None)
    report = None
    if request.method == "POST" and form.is_valid():
        report = EnergyReport.generate(
            form.cleaned_data["start"], form.cleaned_data["end"]
        )
    context = {"form": form, "report": report}
    return render(request, "pages/energy_report.html", context)


def csrf_failure(request, reason=""):
    """Custom CSRF failure view with a friendly message."""
    logger.warning("CSRF failure on %s: %s", request.path, reason)
    return render(request, "pages/csrf_failure.html", status=403)
