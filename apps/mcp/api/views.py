from __future__ import annotations

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.mcp.models import MCPServer


@csrf_exempt
@require_GET
def server_manifest(request: HttpRequest, slug: str):
    server = _get_enabled_server(slug)
    if not server.has_valid_secret(_extract_secret(request)):
        return JsonResponse({"detail": "Invalid or missing MCP secret."}, status=403)

    return JsonResponse(server.manifest(request))


@login_required
@user_passes_test(lambda user: user.is_staff)
@require_POST
def rotate_secret(request: HttpRequest, slug: str):
    server = _get_enabled_server(slug)
    server.api_secret = server._meta.get_field("api_secret").default()
    server.save(update_fields=["api_secret", "updated_at"])
    return JsonResponse(server.manifest(request))


def _get_enabled_server(slug: str) -> MCPServer:
    try:
        server = MCPServer.objects.get(slug=slug)
    except MCPServer.DoesNotExist as exc:  # pragma: no cover - defensive guard
        raise Http404("Unknown MCP server") from exc

    if not server.is_enabled:
        raise Http404("MCP server is disabled")
    return server


def _extract_secret(request: HttpRequest) -> str | None:
    return request.headers.get("X-MCP-Secret") or request.GET.get("secret")
