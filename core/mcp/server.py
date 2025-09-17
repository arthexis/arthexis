"""Integration glue between Django sigils and the MCP FastMCP server."""

from __future__ import annotations

import json
from typing import Any, Mapping
from weakref import WeakKeyDictionary

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


class SigilResolverServer:
    """Factory that wires the sigil resolver into a FastMCP server."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = self._normalize_config(config or {})
        self.catalog = SigilRootCatalog()
        self.service = SigilResolverService(self.catalog)
        self._sessions: WeakKeyDictionary[Any, SigilSessionState] = WeakKeyDictionary()
        self._save_uid = f"mcp_sigil_root_save_{id(self)}"
        self._delete_uid = f"mcp_sigil_root_delete_{id(self)}"
        post_save.connect(self._handle_root_saved, sender=SigilRoot, dispatch_uid=self._save_uid)
        post_delete.connect(self._handle_root_deleted, sender=SigilRoot, dispatch_uid=self._delete_uid)

    def build_fastmcp(self) -> FastMCP:
        """Create and configure a :class:`FastMCP` instance."""

        token_verifier = None
        auth_settings = None
        api_keys = self.config["api_keys"]
        scopes = self.config["required_scopes"]
        if api_keys:
            token_verifier = ApiKeyTokenVerifier.from_keys(api_keys, scopes=scopes)
            base_url = self.config["resource_server_url"] or self._default_base_url()
            issuer_url = self.config["issuer_url"] or base_url
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
            options: ResolveOptions | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            state = self._session_state(ctx)
            result = self.service.resolve_text(
                text,
                session_context=state.context,
                overrides=context,
                options=options,
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
            entries = [root.model_dump(by_alias=True) for root in self.service.list_roots()]
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

    def _default_base_url(self) -> str:
        host = self.config["host"]
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:{self.config['port']}"

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
        }

    def _handle_root_saved(self, sender: type[SigilRoot], instance: SigilRoot, **kwargs: Any) -> None:
        self.catalog.update_from_instance(instance)

    def _handle_root_deleted(self, sender: type[SigilRoot], instance: SigilRoot, **kwargs: Any) -> None:
        self.catalog.remove(instance.prefix)
