import json
from channels.generic.websocket import AsyncWebsocketConsumer

from apps.rates.mixins import RateLimitedConsumerMixin
from config.offline import requires_network

from ... import store
from ...models import Charger
from ..connection import (
    RateLimitedConnectionMixin,
    SubprotocolConnectionMixin,
    WebsocketAuthMixin,
)
from ..constants import (
    OCPP_CONNECT_RATE_LIMIT_FALLBACK,
    OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS,
)
from .auth import AuthMixin
from .certificates import CertificatesMixin
from .connection import ConnectionMixin
from .dispatch import DispatchMixin
from .dispatching import DispatchingMixin
from .identity import IdentityMixin, _resolve_client_ip
from .logging import LoggingMixin
from .transactions import TransactionsMixin


class SinkConsumer(AsyncWebsocketConsumer):
    """Accept any message without validation."""

    rate_limit_scope = "sink-connect"
    rate_limit_fallback = store.MAX_CONNECTIONS_PER_IP
    rate_limit_window = 60

    @requires_network
    async def connect(self) -> None:
        self.client_ip = _resolve_client_ip(self.scope)
        if not await self.enforce_rate_limit():
            return
        await self.accept()

    async def disconnect(self, close_code):
        store.release_ip_connection(getattr(self, "client_ip", None), self)

    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None
    ) -> None:
        if text_data is None:
            return
        try:
            msg = json.loads(text_data)
            if isinstance(msg, list) and msg and msg[0] == 2:
                await self.send(json.dumps([3, msg[1], {}]))
        except Exception:
            pass


class CSMSConsumer(
    IdentityMixin,
    CertificatesMixin,
    DispatchMixin,
    DispatchingMixin,
    AuthMixin,
    ConnectionMixin,
    LoggingMixin,
    TransactionsMixin,
    RateLimitedConnectionMixin,
    SubprotocolConnectionMixin,
    WebsocketAuthMixin,
    RateLimitedConsumerMixin,
    AsyncWebsocketConsumer,
):
    """Very small subset of OCPP 1.6 CSMS behaviour."""

    consumption_update_interval = 300
    rate_limit_target = Charger
    rate_limit_scope = "ocpp-connect"
    rate_limit_fallback = OCPP_CONNECT_RATE_LIMIT_FALLBACK
    rate_limit_window = OCPP_CONNECT_RATE_LIMIT_WINDOW_SECONDS

    def get_rate_limit_identifier(self) -> str | None:
        if self._client_ip_is_local():
            return None
        return super().get_rate_limit_identifier()
