from functools import wraps

from django.http import JsonResponse


def api_login_required(view_func):
    """Require authentication for JSON API views.

    Returns a 401 JSON response when the request user is not authenticated.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"detail": "authentication required"}, status=401)
        return view_func(request, *args, **kwargs)

    _wrapped.login_required = True
    return _wrapped
