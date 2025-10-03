"""Integration glue between Django sigils and the MCP FastMCP server."""

from __future__ import annotations

import json
from typing import Any, Mapping
from weakref import WeakKeyDictionary

from django.contrib.sites.models import Site
from django.core.exceptions import ImproperlyConfigured

from django.db.models.signals import post_delete, post_save

from core.models import SigilRoot

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp.server import Context, FastMCP

from .auth import ApiKeyTokenVerifier
from .schemas import ResolveOptions, ResolveSigilsResponse, SetContextResponse
from .service import (
    ResolutionResult,
    SigilResolverService,
    SigilRootCatalog,
    SigilSessionState,
)


def _normalize_mount_path(value: str | None, *, default: str = "/") -> str:
    """Return a normalized mount path with a leading slash."""

    if not value:
        path = default
    else:
        path = value.strip() or default

    if not path.startswith("/"):
        path = f"/{path}"

    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return path


def _append_mount(base_url: str, mount_path: str) -> str:
    if mount_path == "/":
        return base_url.rstrip("/")
    return f"{base_url.rstrip('/')}{mount_path}"


def resolve_base_urls(config: Mapping[str, Any]) -> tuple[str, str]:
    """Return the public base URLs advertised to MCP clients."""

    port = int(config.get("port", 8800))
    base_url = (config.get("resource_server_url") or "").strip()
    issuer_url = (config.get("issuer_url") or "").strip()
    mount_path = _normalize_mount_path(config.get("mount_path"))

    derived_base = False
    if not base_url:
        base_url = _site_base_url(port) or _host_base_url(config.get("host"), port)
        derived_base = True

    if mount_path != "/" and derived_base:
        base_url = _append_mount(base_url, mount_path)

    if not issuer_url:
        issuer_url = base_url

    return base_url, issuer_url


def _site_base_url(port: int) -> str | None:
    """Derive a base URL from the current ``Site`` domain when available."""

    try:
        site = Site.objects.get_current()
    except (ImproperlyConfigured, Site.DoesNotExist):  # pragma: no cover - defensive
        return None
    except Exception:  # pragma: no cover - database unavailable during startup
        return None

    domain = (site.domain or "").strip()
    if not domain:
        return None

    # Allow administrators to include a full URL in the Sites domain field.
    if "://" in domain:
        return domain.rstrip("/")

    normalized_domain = domain
    host, port_override = _split_host_port(normalized_domain)
    host_for_check = host.strip("[]")
    is_loopback = host_for_check in {"localhost", "::1"} or host_for_check.startswith("127.")

    scheme = "http" if is_loopback else "https"
    if is_loopback:
        port_to_use = port_override or port
    else:
        default_port = 443 if scheme == "https" else 80
        port_to_use = port_override or default_port

    return _build_url(scheme, host, port_to_use)


def _host_base_url(host: str | None, port: int) -> str:
    host = (host or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"

    scheme = "http"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        scheme = "https"

    return _build_url(scheme, host, port)


def _split_host_port(value: str) -> tuple[str, int | None]:
    if value.startswith("[") and "]" in value:
        host, _, remainder = value.partition("]")
        host = f"{host}]"
        if remainder.startswith(":"):
            try:
                return host, int(remainder[1:])
            except ValueError:
                return host, None
        return host, None

    if value.count(":") == 1:
        host, port = value.split(":", 1)
        if port.isdigit():
            return host, int(port)
    return value, None


def _build_url(scheme: str, host: str, port: int) -> str:
    default_port = 443 if scheme == "https" else 80
    formatted_host = host
    if ":" in host and not host.startswith("["):
        formatted_host = f"[{host}]"

    if port == default_port:
        return f"{scheme}://{formatted_host}".rstrip("/")
    return f"{scheme}://{formatted_host}:{port}".rstrip("/")


class SigilResolverServer:
    """Factory that wires the sigil resolver into a FastMCP server."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = self._normalize_config(config or {})
        self.catalog = SigilRootCatalog()
        self.service = SigilResolverService(self.catalog)
        self._sessions: WeakKeyDictionary[Any, SigilSessionState] = WeakKeyDictionary()
        self._save_uid = f"mcp_sigil_root_save_{id(self)}"
        self._delete_uid = f"mcp_sigil_root_delete_{id(self)}"
        post_save.connect(
            self._handle_root_saved, sender=SigilRoot, dispatch_uid=self._save_uid
        )
        post_delete.connect(
            self._handle_root_deleted, sender=SigilRoot, dispatch_uid=self._delete_uid
        )

    def build_fastmcp(self) -> FastMCP:
        """Create and configure a :class:`FastMCP` instance."""

        token_verifier = None
        auth_settings = None
        api_keys = self.config["api_keys"]
        scopes = self.config["required_scopes"]
        if api_keys:
            token_verifier = ApiKeyTokenVerifier.from_keys(api_keys, scopes=scopes)
            base_url, issuer_url = resolve_base_urls(self.config)
            auth_settings = AuthSettings(
                issuer_url=issuer_url,
                resource_server_url=base_url,
                required_scopes=list(token_verifier.scopes),
            )

        server = FastMCP(
            name="Sigil Resolver",
            instructions=self.config["instructions"],
            host=self.config["host"],
            port=self.config["port"],
            mount_path=self.config["mount_path"],
            auth=auth_settings,
            token_verifier=token_verifier,
        )
        self._register_tools(server)
        self._register_resources(server)
        return server

    def _register_tools(self, server: FastMCP) -> None:
        @server.tool(
            name="resolveSigils",
            description="Resolve Arthexis sigils embedded in free-form text.",
        )
        def resolve_sigils(
            text: str,
            context: dict[str, str | int | None] | None = None,
            options: ResolveOptions | Mapping[str, Any] | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            state = self._session_state(ctx)
            resolved_options: ResolveOptions | None = None
            if options is not None:
                if isinstance(options, ResolveOptions):
                    resolved_options = options
                else:
                    resolved_options = ResolveOptions.model_validate(options)
            result = self.service.resolve_text(
                text,
                session_context=state.context,
                overrides=context,
                options=resolved_options,
            )
            return self._format_resolution(result)

        @server.tool(
            name="resolveSingle",
            description="Resolve a single sigil token.",
        )
        def resolve_single(
            sigil: str,
            context: dict[str, str | int | None] | None = None,
            ctx: Context | None = None,
        ) -> str:
            state = self._session_state(ctx)
            return self.service.resolve_single(
                sigil,
                session_context=state.context,
                overrides=context,
            )

        @server.tool(
            name="setContext",
            description="Persist model identifiers for subsequent resolutions within the same session.",
        )
        def set_context(
            context: dict[str, str | int | None],
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            state = self._session_state(ctx)
            stored = self.service.update_session_context(state, context)
            response = SetContextResponse(stored=stored)
            return response.model_dump(by_alias=True)

        @server.tool(
            name="describeSigilRoot",
            description="Return metadata about a configured sigil root.",
        )
        def describe_sigil_root(prefix: str) -> dict[str, Any]:
            description = self.service.describe_root(prefix)
            return description.model_dump(by_alias=True)

    def _register_resources(self, server: FastMCP) -> None:
        @server.resource(
            "resource://sigils/roots",
            name="sigilRoots",
            title="Sigil Roots",
            description="List of configured sigil roots.",
            mime_type="application/json",
        )
        def sigil_roots_resource() -> str:
            entries = [
                root.model_dump(by_alias=True) for root in self.service.list_roots()
            ]
            return json.dumps({"roots": entries}, indent=2)

    def _session_state(self, ctx: Context | None) -> SigilSessionState:
        if ctx is None or ctx.session is None:  # pragma: no cover - defensive guard
            return SigilSessionState()
        session = ctx.session
        state = self._sessions.get(session)
        if state is None:
            state = SigilSessionState()
            self._sessions[session] = state
        return state

    def _format_resolution(self, result: ResolutionResult) -> dict[str, Any]:
        response = ResolveSigilsResponse(
            resolved=result.resolved,
            metadata={"unresolved": result.unresolved},
        )
        return response.model_dump(by_alias=True)

    def _normalize_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        host = config.get("host", "127.0.0.1")
        port = int(config.get("port", 8800))
        instructions = config.get(
            "instructions",
            "Resolve Arthexis sigils over the Model Context Protocol.",
        )
        return {
            "host": host,
            "port": port,
            "api_keys": list(config.get("api_keys", [])),
            "required_scopes": list(config.get("required_scopes", ["sigils:read"])),
            "issuer_url": config.get("issuer_url"),
            "resource_server_url": config.get("resource_server_url"),
            "instructions": instructions,
            "mount_path": _normalize_mount_path(config.get("mount_path")),
        }

    def _handle_root_saved(
        self, sender: type[SigilRoot], instance: SigilRoot, **kwargs: Any
    ) -> None:
        self.catalog.update_from_instance(instance)

    def _handle_root_deleted(
        self, sender: type[SigilRoot], instance: SigilRoot, **kwargs: Any
    ) -> None:
        self.catalog.remove(instance.prefix)
