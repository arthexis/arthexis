from __future__ import annotations

import logging
import uuid
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.contrib.sites.models import Site
from django.utils.translation import gettext

from apps.chats.models import ChatMessage, ChatSession


logger = logging.getLogger(__name__)


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """Realtime chat bridge between visitors and staff."""

    session: ChatSession | None = None
    visitor_key: str = ""
    group_name: str = ""

    async def connect(self):
        if not getattr(settings, "PAGES_CHAT_ENABLED", False):
            await self.close()
            return
        scope_session = self.scope.get("session")
        if scope_session is None:
            await self.close()
            return
        if not scope_session.session_key:
            await database_sync_to_async(scope_session.save)()
        self.visitor_key = scope_session.session_key
        user = self.scope.get("user")
        requested_uuid = self._requested_session_uuid()
        self.session = await self._resolve_session(requested_uuid, user)
        if self.session is None:
            await self.close(code=4403)
            return
        self.group_name = f"chat-session-{self.session.pk}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self._send_history()
        await self._broadcast_presence("join")
        await self._touch_session(user)

    async def disconnect(self, code):  # noqa: D401 - standard channels hook
        try:
            if self.group_name:
                await self.channel_layer.group_discard(
                    self.group_name, self.channel_name
                )
            if self.session:
                await self._broadcast_presence("leave")
        finally:
            self.session = None
            await super().disconnect(code)

    async def receive_json(self, content, **kwargs):
        if not self.session:
            return
        event_type = content.get("type")
        if event_type == "message":
            text = (content.get("content") or "").strip()
            if not text:
                return
            text = text[:2000]
            display_name = (content.get("display_name") or "").strip()
            payload = await self._store_message(text, display_name)
            if payload:
                await self.channel_layer.group_send(
                    self.group_name,
                    {"type": "chat.message", "payload": payload},
                )
        elif event_type == "close" and self.scope.get("user") and self.scope["user"].is_staff:
            await database_sync_to_async(self.session.close)()
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat.message",
                    "payload": {
                        "id": None,
                        "content": gettext("Session closed by staff."),
                        "created": "",
                        "from_staff": True,
                        "author": gettext("System"),
                    },
                },
            )
        else:
            logger.debug("ChatConsumer received unsupported event: %s", content)

    async def chat_message(self, event):
        await self.send_json({"type": "message", **event["payload"]})

    async def chat_presence(self, event):
        await self.send_json(
            {
                "type": "presence",
                "event": event.get("event"),
                "staff": event.get("staff"),
                "author": event.get("author"),
            }
        )

    async def _touch_session(self, user):
        if not self.session:
            return
        await database_sync_to_async(self.session.touch_activity)(
            visitor=not getattr(user, "is_staff", False),
            staff=getattr(user, "is_staff", False),
        )

    async def _store_message(self, text: str, display_name: str):
        if not self.session:
            return None
        user = self.scope.get("user")
        message: ChatMessage = await database_sync_to_async(self.session.add_message)(
            content=text,
            sender=user,
            from_staff=bool(getattr(user, "is_staff", False)),
            display_name=display_name,
        )
        return await database_sync_to_async(message.to_payload)()

    async def _send_history(self):
        if not self.session:
            return
        history = await database_sync_to_async(self._serialize_history)()
        await self.send_json(
            {
                "type": "history",
                "session": str(self.session.uuid),
                "status": self.session.status,
                "messages": history,
            }
        )

    def _serialize_history(self) -> list[dict[str, object]]:
        assert self.session is not None
        return [message.to_payload() for message in self.session.messages.all()]

    async def _broadcast_presence(self, event: str):
        if not self.session:
            return
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "chat.presence",
                "event": event,
                "staff": bool(getattr(self.scope.get("user"), "is_staff", False)),
                "author": self._display_name(),
            },
        )

    def _display_name(self) -> str:
        user = self.scope.get("user")
        if user and getattr(user, "is_authenticated", False):
            full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
            return full_name or user.get_username()
        return gettext("Visitor")

    async def _resolve_session(self, requested_uuid: str | None, user):
        return await database_sync_to_async(self._resolve_session_sync)(
            requested_uuid, user
        )

    def _resolve_session_sync(self, requested_uuid: str | None, user):
        staff = bool(getattr(user, "is_staff", False))
        site = self._current_site()
        session: ChatSession | None = None
        if requested_uuid:
            try:
                uuid.UUID(str(requested_uuid))
            except (TypeError, ValueError):
                requested_uuid = None
            else:
                session = ChatSession.objects.filter(uuid=requested_uuid).first()
                if session and not session.can_join(self.visitor_key, user):
                    return None
        if session is None and not requested_uuid:
            session = (
                ChatSession.objects.filter(
                    visitor_key=self.visitor_key,
                    status__in=[
                        ChatSession.Status.OPEN,
                        ChatSession.Status.ESCALATED,
                    ],
                )
                .order_by("-last_activity_at")
                .first()
            )
        if session is None and requested_uuid:
            # Allow staff to create an empty shell if a stale link was used.
            if staff:
                session = ChatSession.objects.create(
                    site=site,
                    visitor_key="",
                    status=ChatSession.Status.OPEN,
                )
            else:
                return None
        if session is None:
            session = ChatSession.objects.create(
                site=site,
                visitor_key=self.visitor_key,
            )
        updates: list[str] = []
        if not staff and (not session.visitor_key or session.visitor_key != self.visitor_key):
            session.visitor_key = self.visitor_key
            updates.append("visitor_key")
        if session.site_id is None and site is not None:
            session.site = site
            updates.append("site")
        if updates:
            session.save(update_fields=updates)
        session.assign_user(user)
        return session

    def _current_site(self):
        try:
            return Site.objects.get_current()
        except Exception:  # pragma: no cover - Site configuration missing
            return None

    def _requested_session_uuid(self) -> str | None:
        raw_query = self.scope.get("query_string", b"")
        if not raw_query:
            return None
        try:
            params = parse_qs(raw_query.decode("utf-8"), keep_blank_values=False)
        except UnicodeDecodeError:
            return None
        values = params.get("session")
        if not values:
            return None
        return values[0]
