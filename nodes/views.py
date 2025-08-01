import json
import socket

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Node, NodeScreenshot
from .utils import capture_screenshot


def node_list(request):
    """Return a JSON list of all known nodes."""

    nodes = list(
        Node.objects.values("hostname", "address", "port", "last_seen")
    )
    return JsonResponse({"nodes": nodes})


@csrf_exempt
def register_node(request):
    """Register or update a node from POSTed JSON data."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    hostname = data.get("hostname")
    address = data.get("address")
    port = data.get("port", 8000)

    if not hostname or not address:
        return JsonResponse(
            {"detail": "hostname and address required"}, status=400
        )

    node, _ = Node.objects.update_or_create(
        hostname=hostname, defaults={"address": address, "port": port}
    )

    return JsonResponse({"id": node.id})


def capture(request):
    """Capture a screenshot of the site's root URL and record it."""

    url = request.build_absolute_uri("/")
    path = capture_screenshot(url)
    hostname = socket.gethostname()
    node = Node.objects.filter(
        hostname=hostname, port=request.get_port()
    ).first()
    screenshot = NodeScreenshot.objects.create(node=node, path=str(path))
    return JsonResponse(
        {
            "screenshot": str(path),
            "node": screenshot.node.id if screenshot.node else None,
        }
    )
