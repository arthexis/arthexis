"""Authentication helpers for the MCP sigil resolver server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from mcp.server.auth.provider import AccessToken, TokenVerifier


@dataclass(frozen=True)
class ApiKeyTokenVerifier(TokenVerifier):
    """Simple :class:`TokenVerifier` implementation backed by static API keys."""

    keys: Mapping[str, str]
    scopes: tuple[str, ...]

    @classmethod
    def from_keys(
        cls, api_keys: Iterable[str], *, scopes: Iterable[str] | None = None
    ) -> "ApiKeyTokenVerifier":
        keys: dict[str, str] = {}
        for raw_key in api_keys:
            key = raw_key.strip()
            if not key:
                continue
            keys[key] = key
        return cls(keys=keys, scopes=tuple(scopes or ("sigils:read",)))

    async def verify_token(self, token: str) -> AccessToken | None:  # pragma: no cover - thin wrapper
        client_id = self.keys.get(token)
        if client_id is None:
            return None
        return AccessToken(token=token, client_id=client_id, scopes=list(self.scopes))
