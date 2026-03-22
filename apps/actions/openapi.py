"""OpenAPI generation helpers for remote actions."""

from __future__ import annotations

from collections.abc import Iterable
import re

from django.core.exceptions import DisallowedHost
from django.http import HttpRequest
from django.utils.html import strip_tags

from apps.actions.models import RemoteAction


def _queryset_for_user(user):
    """Return active actions available to a concrete user."""

    group_ids = list(user.groups.values_list("id", flat=True)) if user.is_authenticated else []
    return RemoteAction.objects.filter(is_active=True).filter(user=user) | RemoteAction.objects.filter(
        is_active=True, group_id__in=group_ids
    )


def _safe_operation_id(raw_value: str) -> str:
    """Return a safe OpenAPI operationId containing only allowed characters."""

    cleaned = strip_tags(raw_value or "")
    normalized = re.sub(r"[^a-zA-Z0-9._-]", "_", cleaned).strip("_")
    return normalized or "invokeRemoteAction"


def _server_url_for_request(request: HttpRequest | None) -> str:
    """Return an absolute server URL for OpenAPI consumers."""

    if request is None:
        return "http://localhost:8000"
    try:
        return request.build_absolute_uri("/").rstrip("/")
    except DisallowedHost:
        return "http://localhost:8000"


def build_openapi_spec(
    *,
    user,
    actions: Iterable[RemoteAction] | None = None,
    request: HttpRequest | None = None,
) -> dict:
    """Build an OpenAPI 3.1 specification for remote actions."""

    if actions is None:
        selected = _queryset_for_user(user).distinct().order_by("slug")
    else:
        selected = sorted(
            [action for action in actions if action.is_active],
            key=lambda action: action.slug,
        )

    paths: dict[str, dict] = {}
    for action in selected:
        path_key = f"/actions/api/v1/remote/{action.slug}/"
        paths[path_key] = {
            "post": {
                "operationId": _safe_operation_id(action.operation_id),
                "summary": strip_tags(action.display),
                "description": strip_tags(action.description)
                or f"Invoke the `{strip_tags(action.display)}` remote action.",
                "security": [{"bearerAuth": []}],
                "requestBody": {
                    "required": False,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "description": "Optional JSON arguments supplied with the remote action invocation.",
                                "properties": {
                                    "args": {"type": "array", "items": {}},
                                    "kwargs": {"type": "object", "additionalProperties": True},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Remote action invocation accepted.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "action": {"type": "string"},
                                        "args": {"type": "array", "items": {}},
                                        "kwargs": {"type": "object", "additionalProperties": True},
                                    },
                                }
                            }
                        },
                    },
                    "401": {"description": "Unauthorized - Invalid or missing API key"},
                    "403": {"description": "Forbidden - Action not available for this token"},
                },
            }
        }

    paths["/actions/api/v1/security-groups/"] = {
        "get": {
            "operationId": "listSecurityGroups",
            "summary": "List security groups",
            "description": "List security groups for the authenticated user.",
            "security": [{"bearerAuth": []}],
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "groups": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    }
                                },
                            }
                        }
                    },
                },
                "401": {"description": "Unauthorized"},
            },
        }
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Remote Actions API",
            "description": "User-scoped API spec for invoking remote actions.",
            "version": "1.0.0",
        },
        "servers": [{"url": _server_url_for_request(request), "description": "Current server"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "API Key",
                }
            }
        },
    }
