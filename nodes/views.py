import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Node


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
