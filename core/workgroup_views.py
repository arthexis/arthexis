"""REST endpoints for AssistantProfile issuance and authentication."""

from __future__ import annotations

from functools import wraps

from django.apps import apps
from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import AssistantProfile, hash_key


@csrf_exempt
@require_POST
def issue_key(request, user_id: int) -> JsonResponse:
    """Issue a new ``user_key`` for ``user_id``.

    The response reveals the plain key once. Store only the hash server-side.
    """

    user = get_user_model().objects.get(pk=user_id)
    profile, key = AssistantProfile.issue_key(user)
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
            profile = AssistantProfile.objects.get(
                user_key_hash=key_hash, is_active=True
            )
        except AssistantProfile.DoesNotExist:
            return HttpResponse(status=401)

        profile.touch()
        request.assistant_profile = profile
        request.chat_profile = profile
        return view_func(request, *args, **kwargs)

    return wrapper


@require_GET
@authenticate
def assistant_test(request):
    """Return a simple greeting to confirm authentication."""

    profile = getattr(request, "assistant_profile", None)
    user_id = profile.user_id if profile else None
    return JsonResponse({"message": f"Hello from user {user_id}"})


@require_GET
@authenticate
def chat(request):
    """Return serialized data from any model.

    Clients must provide ``model`` as ``app_label.ModelName`` and may include a
    ``pk`` to fetch a specific record. When ``pk`` is omitted, the view returns
    up to 100 records.
    """

    model_label = request.GET.get("model")
    if not model_label:
        return JsonResponse({"error": "model parameter required"}, status=400)
    try:
        model = apps.get_model(model_label)
    except LookupError:
        return JsonResponse({"error": "unknown model"}, status=400)

    qs = model.objects.all()
    pk = request.GET.get("pk")
    if pk is not None:
        try:
            obj = qs.get(pk=pk)
        except model.DoesNotExist:
            return JsonResponse({"error": "object not found"}, status=404)
        data = model_to_dict(obj)
    else:
        data = [model_to_dict(o) for o in qs[:100]]

    return JsonResponse({"data": data})
