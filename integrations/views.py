from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from utils.api import api_login_required

from .services import post_from_domain, post_from_user, register_account


@require_POST
@api_login_required
def register(request):
    handle = request.POST["handle"]
    password = request.POST["app_password"]
    register_account(request.user, handle, password)
    return JsonResponse({"status": "ok"})


@require_POST
@api_login_required
def post(request):
    text = request.POST["text"]
    post_from_user(request.user, text)
    return JsonResponse({"status": "ok"})


@require_POST
@api_login_required
@staff_member_required
def domain_post(request):
    text = request.POST["text"]
    post_from_domain(text)
    return JsonResponse({"status": "ok"})

import xmlrpc.client
from urllib.parse import urljoin
from django.shortcuts import get_object_or_404

from config.offline import requires_network
from .models import Instance


@api_login_required
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
