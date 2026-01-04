from __future__ import annotations

from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import MCPServer


@csrf_exempt
@require_POST
def rpc_gateway(request: HttpRequest, slug: str):
    server = _get_enabled_server(slug)
    if not server.has_valid_secret(_extract_secret(request)):
        return JsonResponse({"detail": "Invalid or missing MCP secret."}, status=403)

    return JsonResponse(
        {
            "status": "ready",
            "server": server.slug,
            "acting_user": getattr(server.acting_user, "username", ""),
        }
    )


@csrf_exempt
@require_POST
def event_sink(request: HttpRequest, slug: str):
    server = _get_enabled_server(slug)
    if not server.has_valid_secret(_extract_secret(request)):
        return JsonResponse({"detail": "Invalid or missing MCP secret."}, status=403)

    return JsonResponse(
        {
            "status": "accepted",
            "server": server.slug,
        },
        status=202,
    )


@require_GET
def health(request: HttpRequest, slug: str):
    server = _get_enabled_server(slug)
    return JsonResponse({"status": "ok", "server": server.slug})


def _get_enabled_server(slug: str) -> MCPServer:
    try:
        server = MCPServer.objects.get(slug=slug)
    except MCPServer.DoesNotExist as exc:  # pragma: no cover - defensive guard
        raise Http404("Unknown MCP server") from exc

    if not server.is_enabled:
        raise Http404("MCP server is disabled")
    return server


def _extract_secret(request: HttpRequest) -> str | None:
    return request.headers.get("X-MCP-Secret") or request.POST.get("secret")
