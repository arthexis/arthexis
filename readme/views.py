from pathlib import Path
from django.conf import settings
from django.http import HttpResponse
import markdown


def readme(request):
    readme_path = Path(settings.BASE_DIR) / "README.md"
    text = readme_path.read_text(encoding="utf-8")
    html = markdown.markdown(text, extensions=["tables"])
    return HttpResponse(html)
