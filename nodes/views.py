import json
import socket

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404

from .models import Node, NodeScreenshot, NodeMessage
from .utils import capture_screenshot, save_screenshot


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
    screenshot = save_screenshot(path, node=node, method=request.method)
    node_id = None
    if screenshot and screenshot.node:
        node_id = screenshot.node.id
    elif node:
        node_id = node.id
    return JsonResponse({"screenshot": str(path), "node": node_id})


@csrf_exempt
def public_node_endpoint(request, endpoint):
    """Public API endpoint for a node.

    - ``GET`` returns information about the node.
    - ``POST`` stores the request as a :class:`NodeMessage`.
    """

    node = get_object_or_404(
        Node, public_endpoint=endpoint, enable_public_api=True
    )

    if request.method == "GET":
        data = {
            "hostname": node.hostname,
            "address": node.address,
            "port": node.port,
            "badge_color": node.badge_color,
            "last_seen": node.last_seen,
        }
        return JsonResponse(data)

    if request.method == "POST":
        NodeMessage.objects.create(
            node=node,
            method=request.method,
            headers=dict(request.headers),
            body=request.body.decode("utf-8") if request.body else "",
        )
        return JsonResponse({"status": "stored"})

    return JsonResponse({"detail": "Method not allowed"}, status=405)
