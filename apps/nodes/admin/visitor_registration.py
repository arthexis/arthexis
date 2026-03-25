from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from django.test.client import RequestFactory
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.nodes.views.registration import register_visitor_proxy


@dataclass(slots=True)
class VisitorRegistrationRequest:
    token: str
    visitor_base: str | None
    visitor_error: str | None
    visitor_host: str
    visitor_info_url: str
    visitor_port: int | None
    visitor_register_url: str
    visitor_scheme: str

    @classmethod
    def from_http_request(cls, request, *, default_port: int = 443) -> VisitorRegistrationRequest:
        token = (request.POST.get("token") if request.method == "POST" else None) or uuid.uuid4().hex
        query_host = str(request.GET.get("visitor") or "").strip()
        post_host = str(request.POST.get("visitor_host") or "").strip() if request.method == "POST" else ""
        raw_host = post_host or query_host or "127.0.0.1"

        submitted_port = request.POST.get("visitor_port") if request.method == "POST" else None
        port, invalid_port = cls._parse_port(submitted_port)
        base, host, parsed_port, scheme = cls._build_base(raw_host, port, default_port=default_port)

        visitor_error = None
        if invalid_port:
            visitor_error = _("Visitor port is invalid. Use a value between 1 and 65535.")
        elif not base:
            visitor_error = _("Visitor address missing. Reload with ?visitor=host[:port].")

        return cls(
            token=token,
            visitor_base=base,
            visitor_error=visitor_error,
            visitor_host=host,
            visitor_info_url=f"{base}/nodes/info/" if base else "",
            visitor_port=parsed_port,
            visitor_register_url=f"{base}/nodes/register/" if base else "",
            visitor_scheme=scheme,
        )

    @staticmethod
    def _parse_port(raw_port: str | None) -> tuple[int | None, bool]:
        if raw_port in (None, ""):
            return None, False
        try:
            parsed = int(raw_port)
        except (TypeError, ValueError):
            return None, True
        if parsed < 1 or parsed > 65535:
            return None, True
        return parsed, False

    @staticmethod
    def _build_base(
        raw_host: str,
        port_override: int | None,
        *,
        default_port: int,
    ) -> tuple[str | None, str, int | None, str]:
        candidate = (raw_host or "").strip()
        if not candidate:
            return None, "", None, "https"

        if "://" not in candidate:
            candidate = f"https://{candidate.lstrip('/')}"

        try:
            parsed = urlsplit(candidate)
        except ValueError:
            return None, "", None, "https"

        hostname = parsed.hostname or ""
        if not hostname:
            return None, "", None, "https"

        try:
            parsed_port = parsed.port
        except ValueError:
            return None, hostname, None, "https"

        scheme = (parsed.scheme or "https").lower()
        if scheme != "https":
            scheme = "https"

        port = port_override or parsed_port or default_port
        host_part = f"[{hostname}]" if ":" in hostname else hostname
        return urlunsplit((scheme, f"{host_part}:{port}", "", "", "")), hostname, port, scheme


@dataclass(slots=True)
class VisitorRegistrationResult:
    errors: list[str] = field(default_factory=list)
    host: dict = field(default_factory=dict)
    status: str = "idle"
    summary: dict | None = None
    visitor: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class VisitorRegistrationService:
    def __init__(self, *, user):
        self.user = user

    def register(self, parsed_request: VisitorRegistrationRequest) -> VisitorRegistrationResult:
        result = VisitorRegistrationResult()

        if parsed_request.visitor_error:
            result.status = "error"
            result.errors.append(str(parsed_request.visitor_error))
            result.summary = {"status": "error", "message": str(parsed_request.visitor_error)}
            result.host = {"status": "error", "message": str(parsed_request.visitor_error)}
            result.visitor = {"status": "error", "message": str(parsed_request.visitor_error)}
            return result

        payload = json.dumps(
            {
                "token": parsed_request.token,
                "visitor_info_url": parsed_request.visitor_info_url,
                "visitor_register_url": parsed_request.visitor_register_url,
            }
        )
        proxy_request = RequestFactory().post(
            reverse("register-visitor-proxy"),
            data=payload,
            content_type="application/json",
        )
        proxy_request.user = self.user
        proxy_request._cached_user = self.user
        proxy_response = register_visitor_proxy(proxy_request)

        try:
            proxy_body = json.loads(proxy_response.content.decode() or "{}")
        except json.JSONDecodeError:
            proxy_body = None

        if proxy_body is None:
            message = str(_("Registration proxy returned an invalid response."))
            result.status = "error"
            result.errors.append(message)
            result.summary = {"status": "error", "message": message}
            result.host = {"status": "error", "message": message}
            result.visitor = {"status": "error", "message": message}
            return result

        if proxy_response.status_code == 200 and proxy_body.get("host") and proxy_body.get("visitor"):
            host_body = proxy_body.get("host", {})
            visitor_body = proxy_body.get("visitor", {})
            if not proxy_body.get("host_requires_https", True):
                result.warnings.append(
                    str(_("Host node is not configured to require HTTPS. Update its Sites settings."))
                )
            if not proxy_body.get("visitor_requires_https", True):
                result.warnings.append(
                    str(_("Visitor node is not configured to require HTTPS. Update its Sites settings."))
                )
            result.status = "success"
            result.summary = {
                "status": "success",
                "message": str(_("Both nodes registered successfully.")),
            }
            result.host = {
                "status": "success",
                "message": host_body.get("detail") or str(_("Visitor node registered with this server.")),
                "id": host_body.get("id"),
            }
            result.visitor = {
                "status": "success",
                "message": visitor_body.get("detail") or str(_("Host node registered with visitor.")),
                "id": visitor_body.get("id"),
            }
            return result

        error_message = str(proxy_body.get("detail") or _("Registration aborted."))
        result.status = "error"
        result.errors.append(error_message)
        result.summary = {"status": "error", "message": error_message}
        result.host = {"status": "error", "message": error_message}
        result.visitor = {"status": "error", "message": error_message}
        return result
