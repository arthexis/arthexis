from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model, login
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django import forms
from utils.sites import get_site
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.core.mail import send_mail
from django.utils.translation import gettext as _

import markdown
from website.utils import landing


@landing("Home")
def index(request):
    site = get_site(request)
    app = (
        site.site_applications.filter(is_default=True)
        .select_related("application")
        .first()
    )
    app_slug = app.path.strip("/") if app else ""
    readme_base = Path(settings.BASE_DIR) / app_slug if app_slug else Path(settings.BASE_DIR)
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
    return render(request, "website/readme.html", context)


def sitemap(request):
    site = get_site(request)
    applications = site.site_applications.all()
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

    template_name = "website/login.html"

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


def request_invite(request):
    form = InvitationRequestForm(request.POST if request.method == "POST" else None)
    sent = False
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        User = get_user_model()
        for user in User.objects.filter(email__iexact=email):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            link = request.build_absolute_uri(
                reverse("website:invitation-login", args=[uid, token])
            )
            send_mail(
                _("Your invitation link"),
                _("Use the following link to access your account: %(link)s")
                % {"link": link},
                None,
                [email],
            )
        sent = True
    return render(request, "website/request_invite.html", {"form": form, "sent": sent})


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
        login(request, user, backend="accounts.backends.LocalhostAdminBackend")
        return redirect(reverse("admin:index") if user.is_staff else "/")
    return render(request, "website/invitation_login.html", {"form": form})


@staff_member_required
def admin_console(request):
    return render(request, "admin/console.html")


def csrf_failure(request, reason=""):
    """Custom CSRF failure view with a friendly message."""
    return render(request, "website/csrf_failure.html", status=403)

