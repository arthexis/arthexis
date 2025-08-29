import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404

from utils.api import api_login_required

from .models import Node, Message
from .utils import capture_screenshot, save_screenshot


@api_login_required
def node_list(request):
    """Return a JSON list of all known nodes."""

    nodes = list(
        Node.objects.values("hostname", "address", "port", "last_seen")
    )
    return JsonResponse({"nodes": nodes})


@csrf_exempt
@api_login_required
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
    mac_address = data.get("mac_address")

    if not hostname or not address or not mac_address:
        return JsonResponse(
            {"detail": "hostname, address and mac_address required"}, status=400
        )

    mac_address = mac_address.lower()
    node, created = Node.objects.get_or_create(
        mac_address=mac_address,
        defaults={"hostname": hostname, "address": address, "port": port},
    )
    if not created:
        node.hostname = hostname
        node.address = address
        node.port = port
        node.save(update_fields=["hostname", "address", "port"])
        return JsonResponse(
            {"id": node.id, "detail": f"Node already exists (id: {node.id})"}
        )

    return JsonResponse({"id": node.id})


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


@csrf_exempt
@api_login_required
def public_node_endpoint(request, endpoint):
    """Public API endpoint for a node.

    - ``GET`` returns information about the node.
    - ``POST`` stores the request as a :class:`nodes.models.Message`.
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
        Message.objects.create(
            node=node,
            subject=request.method,
            body=request.body.decode("utf-8") if request.body else "",
        )
        return JsonResponse({"status": "stored"})

    return JsonResponse({"detail": "Method not allowed"}, status=405)
