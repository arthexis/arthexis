from __future__ import annotations

import json

from django.contrib.auth import REDIRECT_FIELD_NAME, authenticate, login
from django.http import JsonResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt

from config.request_utils import is_https_request


@csrf_exempt
def rfid_login(request):
    """Authenticate a user using an RFID."""

    if request.method != "POST":
        return JsonResponse({"detail": "POST required"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except json.JSONDecodeError:
        data = request.POST

    rfid = data.get("rfid")
    if not rfid:
        return JsonResponse({"detail": "rfid required"}, status=400)

    redirect_to = data.get(REDIRECT_FIELD_NAME) or data.get("next")
    if redirect_to and not url_has_allowed_host_and_scheme(
        redirect_to,
        allowed_hosts={request.get_host()},
        require_https=is_https_request(request),
    ):
        redirect_to = ""

    user = authenticate(request, rfid=rfid)
    if user is None:
        return JsonResponse({"detail": "invalid RFID"}, status=401)

    login(request, user)
    if redirect_to:
        target = redirect_to
    elif user.is_staff:
        target = reverse("admin:index")
    else:
        target = "/"
    return JsonResponse(
        {"id": user.id, "username": user.username, "redirect": target}
    )
