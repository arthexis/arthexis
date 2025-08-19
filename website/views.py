from pathlib import Path

from django.conf import settings
from django.contrib.auth.views import LoginView
from utils.sites import get_site
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import translation

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
