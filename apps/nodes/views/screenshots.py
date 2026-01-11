from django.http import JsonResponse

from utils.api import api_login_required

from ..models import Node
from apps.content.utils import capture_screenshot, save_screenshot


@api_login_required
def capture(request):
    """Capture a screenshot of the site's root URL and record it."""

    url = request.build_absolute_uri("/")
    try:
        path = capture_screenshot(url)
    except Exception as exc:  # pragma: no cover - depends on selenium setup
        return JsonResponse({"detail": str(exc)}, status=500)
    node = Node.get_local()
    screenshot = save_screenshot(path, node=node, method=request.method)
    node_id = screenshot.node.id if screenshot and screenshot.node else None
    return JsonResponse({"screenshot": str(path), "node": node_id})
