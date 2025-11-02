from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from graphene_django.views import GraphQLView

from .schema import schema

_TOKEN_PREFIXES = ("token ", "bearer ")


def _uses_token_auth(request: HttpRequest) -> bool:
    """Return ``True`` when the request carries an authorization token."""

    header = request.META.get("HTTP_AUTHORIZATION", "")
    return header.lower().startswith(_TOKEN_PREFIXES)


class EnergyGraphQLView(GraphQLView):
    """GraphQL view that conditionally skips CSRF checks for token clients."""

    def __init__(self, **kwargs):
        kwargs.setdefault("schema", schema)
        kwargs.setdefault("graphiql", settings.DEBUG)
        super().__init__(**kwargs)
        self.csrf_exempt = False

    @method_decorator(csrf_protect)
    def dispatch(self, request: HttpRequest, *args, **kwargs):  # pragma: no cover - Django handles dispatch
        if _uses_token_auth(request):
            setattr(request, "_dont_enforce_csrf_checks", True)
        return super().dispatch(request, *args, **kwargs)
