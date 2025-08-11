from pathlib import Path
from importlib import import_module
import re

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.contrib.sites.shortcuts import get_current_site
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

import markdown
from website.utils import landing


@landing("Home")
def index(request):
    site = get_current_site(request)
    app = (
        site.site_applications.filter(is_default=True)
        .select_related("application")
        .first()
    )
    app_slug = app.path.strip("/") if app else ""
    readme_file = (
        Path(settings.BASE_DIR) / app_slug / "README.md"
        if app_slug
        else Path(settings.BASE_DIR) / "README.md"
    )
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
    context = {"content": html, "title": readme_file.stem, "toc": toc_html}
    return render(request, "website/readme.html", context)


def sitemap(request):
    site = get_current_site(request)
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


def app_index(request, module):
    """Render a simple index for an application's URL patterns.

    Displays a card for each pattern in the module's ``urlpatterns`` so users can
    easily navigate to available views. Dynamic routes are filled with placeholder
    values to provide functional example links.
    """

    mod = import_module(module)
    patterns = getattr(mod, "urlpatterns", [])
    base = request.path if request.path.endswith("/") else f"{request.path}/"
    entries = []

    for pattern in patterns:
        route = getattr(getattr(pattern, "pattern", None), "_route", "")
        if not route:
            continue
        url_route = route
        if "<" in url_route:
            # Replace converters with placeholder values
            url_route = re.sub(r"<int:[^>]+>", "1", url_route)
            url_route = re.sub(r"<slug:[^>]+>", "example", url_route)
            url_route = re.sub(r"<str:[^>]+>", "example", url_route)
            url_route = re.sub(r"<[^>]+>", "example", url_route)
        name = pattern.name or getattr(pattern.callback, "__name__", route)
        label = name.replace("-", " ").replace("_", " ").title()
        entries.append({"label": label, "url": f"{base}{url_route}"})

    app_label = module.split(".")[-2].replace("_", " ").title()
    context = {"entries": entries, "app_label": app_label}
    return render(request, "website/app_index.html", context)


class CustomLoginView(LoginView):
    """Login view that redirects staff to the admin."""

    template_name = "website/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.user.is_staff:
                return redirect("admin:index")
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        if self.request.user.is_staff:
            return reverse("admin:index")
        return self.get_redirect_url() or "/"


login_view = CustomLoginView.as_view()
