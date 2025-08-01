import xmlrpc.client
from urllib.parse import urljoin

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from .models import Instance
from config.offline import requires_network


@requires_network
def test_connection(request, pk):
    instance = get_object_or_404(Instance, pk=pk)
    server = xmlrpc.client.ServerProxy(urljoin(instance.url, "/xmlrpc/2/common"))
    try:
        uid = server.authenticate(instance.database, instance.username, instance.password, {})
    except Exception as exc:  # pragma: no cover - network errors
        return JsonResponse({"detail": str(exc)}, status=400)
    if uid:
        return JsonResponse({"detail": "success"})
    return JsonResponse({"detail": "invalid credentials"}, status=401)
