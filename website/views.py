from pathlib import Path
from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.urls import reverse
from django.contrib.auth.views import LoginView

import inspect
import markdown
from config import urls as project_urls
from django.urls.resolvers import URLResolver, URLPattern
from website.utils import landing
from footer.utils import footer_link


def _collect_landings(resolver: URLResolver, prefix: str = ""):
    pages = []
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            pages.extend(_collect_landings(pattern, prefix + pattern.pattern._route))
        elif isinstance(pattern, URLPattern):
            view = pattern.callback
            if getattr(view, "landing", False):
                route = prefix + pattern.pattern._route
                if "<" in route:
                    continue
                label = getattr(view, "landing_label", pattern.name or route)
                pages.append({"name": label, "path": "/" + route})
    return pages


def get_landing_apps():
    apps = []
    for p in project_urls.urlpatterns:
        if isinstance(p, URLResolver):
            prefix = p.pattern._route
            if prefix.startswith("admin"):
                continue
            module = p.urlconf_module
            name = (
                module.__package__.split(".")[0]
                if inspect.ismodule(module)
                else str(module).split(".")[0]
            )
            pages = _collect_landings(p, prefix)
            if pages:
                apps.append({"name": name.capitalize(), "views": pages})
    return apps


@landing("Home")
def index(request):
    site = get_current_site(request)
    app_name = site.name or "readme"
    readme_file = Path(settings.BASE_DIR) / (
        "README.md" if app_name == "readme" else Path(app_name) / "README.md"
    )
    if not readme_file.exists():
        readme_file = Path(settings.BASE_DIR) / "README.md"
    text = readme_file.read_text(encoding="utf-8")
    html = markdown.markdown(text)
    context = {"content": html, "title": readme_file.stem}
    if app_name in ("website", "readme"):
        context["nav_apps"] = get_landing_apps()
    return render(request, "website/readme.html", context)


@footer_link("Sitemap")
def sitemap(request):
    apps = get_landing_apps()
    base = request.build_absolute_uri("/").rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    seen = set()
    for app in apps:
        for page in app["views"]:
            loc = f"{base}{page['path']}"
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
            if request.user.is_staff:
                return redirect("admin:index")
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        if self.request.user.is_staff:
            return reverse("admin:index")
        return self.get_redirect_url() or "/"


login_view = CustomLoginView.as_view()
