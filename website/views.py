from pathlib import Path
from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site
from django.shortcuts import render
import markdown


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
    return render(
        request,
        "website/readme.html",
        {"content": html, "title": readme_file.stem},
    )
