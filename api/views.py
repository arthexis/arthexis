from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.views import View

try:  # pragma: no cover - exercised indirectly in tests
    from graphene_django.views import GraphQLView as _GraphQLView
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    _GraphQLView = None

from .schema import schema

_TOKEN_PREFIXES = ("token ", "bearer ")


def _uses_token_auth(request: HttpRequest) -> bool:
    """Return ``True`` when the request carries an authorization token."""

    header = request.META.get("HTTP_AUTHORIZATION", "")
    return header.lower().startswith(_TOKEN_PREFIXES)


class EnergyGraphQLView(View if _GraphQLView is None else _GraphQLView):
    """GraphQL view that conditionally skips CSRF checks for token clients."""

    csrf_exempt = True

    def __init__(self, **kwargs):
        if _GraphQLView is None:
            super().__init__()
            self.csrf_exempt = False
            return

        kwargs.setdefault("schema", schema)
        kwargs.setdefault("graphiql", settings.DEBUG)
        super().__init__(**kwargs)
        self.csrf_exempt = True

    def dispatch(self, request: HttpRequest, *args, **kwargs):  # pragma: no cover - Django handles dispatch
        if _uses_token_auth(request):
            setattr(request, "_dont_enforce_csrf_checks", True)
            setattr(request, "csrf_processing_done", True)
        elif request.method.upper() == "POST":
            setattr(request, "csrf_processing_done", True)
            return JsonResponse(
                {"errors": [{"message": "CSRF Failed: Token missing."}]}, status=403
            )

        if _GraphQLView is not None:
            return super().dispatch(request, *args, **kwargs)

        return View.dispatch(self, request, *args, **kwargs)

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:  # pragma: no cover - error path
        if _GraphQLView is not None:
            return super().get(request, *args, **kwargs)
        return JsonResponse({"errors": [{"message": "GET not supported."}]}, status=405)

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if _GraphQLView is not None:
            return super().post(request, *args, **kwargs)

        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse(
                {"errors": [{"message": "Invalid JSON payload."}]}, status=400
            )

        query = payload.get("query", "")
        variables = payload.get("variables")
        result = schema.execute(query, variable_values=variables, context_value=request)

        data = getattr(result, "data", None)
        errors = getattr(result, "errors", None)

        error_payload = None
        if errors:
            error_payload = []
            for item in errors:
                if isinstance(item, dict):
                    error_payload.append(item)
                else:
                    message = getattr(item, "message", str(item))
                    error_payload.append({"message": message})

        response_payload = {}
        if error_payload:
            response_payload["errors"] = error_payload
        if data is not None:
            response_payload["data"] = data

        return JsonResponse(response_payload, status=200)
