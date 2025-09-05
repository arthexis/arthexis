"""REST endpoints for ChatProfile issuance and authentication."""

from __future__ import annotations

from functools import wraps

from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import ChatProfile, hash_key


@csrf_exempt
@require_POST
def issue_key(request, user_id: int) -> JsonResponse:
    """Issue a new ``user_key`` for ``user_id``.

    The response reveals the plain key once. Store only the hash server-side.
    """

    user = get_user_model().objects.get(pk=user_id)
    profile, key = ChatProfile.issue_key(user)
    return JsonResponse({"user_id": user_id, "user_key": key})


def authenticate(view_func):
    """View decorator that validates the ``Authorization`` header."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return HttpResponse(status=401)

        key_hash = hash_key(header.split(" ", 1)[1])
        try:
            profile = ChatProfile.objects.get(user_key_hash=key_hash, is_active=True)
        except ChatProfile.DoesNotExist:
            return HttpResponse(status=401)

        profile.touch()
        request.chat_profile = profile
        return view_func(request, *args, **kwargs)

    return wrapper


@require_GET
@authenticate
def assistant_test(request):
    """Return a simple greeting to confirm authentication."""

    user_id = request.chat_profile.user_id
    return JsonResponse({"message": f"Hello from user {user_id}"})

