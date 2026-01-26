from __future__ import annotations

import base64
import binascii
from collections import deque

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.contrib.auth import authenticate

from apps.rates.models import RateLimit

from .. import store
from .constants import OCPP_VERSION_16, OCPP_VERSION_201, OCPP_VERSION_21
from .ip_utils import _parse_ip


class RateLimitedConnectionMixin:
    def _client_ip_is_local(self) -> bool:
        parsed = _parse_ip(getattr(self, "client_ip", None))
        if not parsed:
            return False
        return parsed.is_private or parsed.is_loopback or parsed.is_link_local

    async def _accept_connection(self, subprotocol: str | None) -> bool:
        """Accept the websocket connection after rate limits are enforced."""

        existing = store.connections.get(self.store_key)
        replacing_existing = existing is not None
        if existing is not None:
            store.release_ip_connection(getattr(existing, "client_ip", None), existing)
            await existing.close()
        should_enforce_rate_limit = True
        if replacing_existing and getattr(existing, "client_ip", None) == self.client_ip:
            should_enforce_rate_limit = await self._has_rate_limit_rule()
        if should_enforce_rate_limit and not await self.enforce_rate_limit():
            store.add_log(
                self.store_key,
                f"Rejected connection from {self.client_ip or 'unknown'}: rate limit exceeded",
                log_type="charger",
            )
            return False
        await self.accept(subprotocol=subprotocol)
        store.add_log(
            self.store_key,
            f"Connected (subprotocol={subprotocol or 'none'})",
            log_type="charger",
        )
        store.connections[self.store_key] = self
        store.logs["charger"].setdefault(
            self.store_key, deque(maxlen=store.MAX_IN_MEMORY_LOG_ENTRIES)
        )
        return True

    async def _has_rate_limit_rule(self) -> bool:
        def _resolve_rule() -> bool:
            return (
                RateLimit.for_target(
                    self.get_rate_limit_target(), scope_key=self.rate_limit_scope
                )
                is not None
            )

        return await database_sync_to_async(_resolve_rule)()


class SubprotocolConnectionMixin:
    def _select_subprotocol(
        self, offered: list[str] | tuple[str, ...], preferred: str | None
    ) -> str | None:
        """Choose the negotiated OCPP subprotocol, honoring stored preference."""

        available: list[str] = []
        for proto in offered:
            if not proto:
                continue
            if isinstance(proto, bytes):
                try:
                    proto_text = proto.decode("latin1")
                except Exception:
                    continue
            else:
                proto_text = str(proto)
            proto_text = proto_text.strip()
            if proto_text:
                available.append(proto_text)
        preferred_normalized = (preferred or "").strip()
        if preferred_normalized and preferred_normalized in available:
            return preferred_normalized
        # Prefer the latest supported OCPP 2.x protocol when the charger
        # requests it, otherwise fall back to older versions.
        if OCPP_VERSION_21 in available:
            return OCPP_VERSION_21
        if OCPP_VERSION_201 in available:
            return OCPP_VERSION_201
        # Operational safeguard: never reject a charger solely because it omits
        # or sends an unexpected subprotocol.  We negotiate ``ocpp1.6`` when the
        # charger offers it, but otherwise continue without a subprotocol so we
        # accept as many real-world stations as possible.
        if OCPP_VERSION_16 in available:
            return OCPP_VERSION_16
        return None

    def _get_offered_subprotocols(self) -> list[str]:
        """Return the subprotocols offered by the connecting websocket client."""

        offered = self.scope.get("subprotocols") or []
        normalized: list[str] = []
        for proto in offered:
            try:
                if isinstance(proto, (bytes, bytearray)):
                    value = proto.decode("latin1")
                else:
                    value = str(proto)
            except (AttributeError, TypeError, UnicodeDecodeError):
                continue
            value = value.strip()
            if value:
                normalized.append(value)
        if normalized:
            return normalized

        headers = self.scope.get("headers") or []
        for raw_name, raw_value in headers:
            if not isinstance(raw_name, (bytes, bytearray)):
                continue
            if raw_name.lower() != b"sec-websocket-protocol":
                continue
            try:
                header_value = raw_value.decode("latin1")
                for candidate in header_value.split(","):
                    trimmed = candidate.strip()
                    if trimmed:
                        normalized.append(trimmed)
            except (AttributeError, TypeError, UnicodeDecodeError):
                continue
        return normalized

    def _negotiate_ocpp_version(self, existing_charger: "Charger" | None) -> str | None:
        """Resolve the negotiated OCPP subprotocol and set version attributes."""

        preferred_version = (
            existing_charger.preferred_ocpp_version_value()
            if existing_charger
            else ""
        )
        offered = self._get_offered_subprotocols()
        subprotocol = self._select_subprotocol(offered, preferred_version)
        self.preferred_ocpp_version = preferred_version
        negotiated_version = subprotocol
        if not negotiated_version and preferred_version in {
            OCPP_VERSION_201,
            OCPP_VERSION_21,
        }:
            negotiated_version = preferred_version
        self.ocpp_version = negotiated_version or OCPP_VERSION_16
        return subprotocol


class WebsocketAuthMixin:
    def _parse_basic_auth_header(self) -> tuple[tuple[str, str] | None, str | None]:
        """Return decoded Basic auth credentials and an error code if any."""

        headers = self.scope.get("headers") or []
        for raw_name, raw_value in headers:
            if not isinstance(raw_name, (bytes, bytearray)):
                continue
            if raw_name.lower() != b"authorization":
                continue
            if not isinstance(raw_value, (bytes, bytearray)):
                return None, "invalid"
            try:
                header_value = raw_value.decode("latin1")
            except UnicodeDecodeError:
                return None, "invalid"
            scheme, _, param = header_value.partition(" ")
            if scheme.lower() != "basic" or not param:
                return None, "invalid"
            try:
                decoded = base64.b64decode(param.strip(), validate=True).decode(
                    "utf-8"
                )
            except (binascii.Error, UnicodeDecodeError):
                return None, "invalid"
            username, sep, password = decoded.partition(":")
            if not sep:
                return None, "invalid"
            return (username, password), None
        return None, "missing"

    async def _authenticate_basic_credentials(self, username: str, password: str):
        """Return the authenticated user for HTTP Basic credentials, if valid."""

        if username is None or password is None:
            return None

        user = await sync_to_async(authenticate)(
            request=None, username=username, password=password
        )
        if user is None or not getattr(user, "is_active", False):
            return None
        return user

    async def _enforce_ws_auth(self, existing_charger: "Charger" | None) -> bool:
        """Enforce HTTP Basic auth requirements for websocket connections."""

        if not existing_charger or not existing_charger.requires_ws_auth:
            return True
        credentials, error_code = self._parse_basic_auth_header()
        rejection_reason: str | None = None
        if error_code == "missing":
            rejection_reason = "HTTP Basic authentication required (credentials missing)"
        elif error_code == "invalid":
            rejection_reason = "HTTP Basic authentication header is invalid"
        else:
            username, password = credentials
            auth_user = await self._authenticate_basic_credentials(
                username, password
            )
            if auth_user is None:
                rejection_reason = "HTTP Basic authentication failed"
            else:
                authorized = await database_sync_to_async(
                    existing_charger.is_ws_user_authorized
                )(auth_user)
                if not authorized:
                    user_label = getattr(auth_user, "get_username", None)
                    if callable(user_label):
                        user_label = user_label()
                    else:
                        user_label = getattr(auth_user, "username", "")
                    if user_label:
                        rejection_reason = (
                            "HTTP Basic authentication rejected for unauthorized user "
                            f"'{user_label}'"
                        )
                    else:
                        rejection_reason = (
                            "HTTP Basic authentication rejected for unauthorized user"
                        )
        if rejection_reason:
            store.add_log(
                self.store_key,
                f"Rejected connection: {rejection_reason}",
                log_type="charger",
            )
            await self.close(code=4003)
            return False
        return True
