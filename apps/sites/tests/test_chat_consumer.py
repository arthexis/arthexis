from types import SimpleNamespace
from unittest import TestCase, mock

from asgiref.sync import async_to_sync
from django.core.cache import cache

from apps.sites.consumers import (
    ChatConsumer,
    DEFAULT_PRESENCE_FLAP_WINDOW_SECONDS,
)


class ChatConsumerPresenceDebounceTests(TestCase):
    """Coverage for suppressing noisy chat presence chatter."""

    def setUp(self):
        """Reset cache state before each test to keep debounce assertions deterministic."""

        cache.clear()

    def _build_consumer(self, *, session_pk: int) -> ChatConsumer:
        """Create a consumer bound to a minimal session-like object."""

        consumer = ChatConsumer()
        consumer.session = SimpleNamespace(pk=session_pk)
        return consumer

    def test_should_emit_presence_suppresses_rapid_staff_join_leave_flapping(self):
        """Regression: rapid staff reconnect churn should not spam presence notices."""

        consumer = self._build_consumer(session_pk=101)

        with (
            mock.patch(
                "apps.sites.consumers.settings.PAGES_CHAT_PRESENCE_FLAP_WINDOW_SECONDS",
                1,
                create=True,
            ),
            mock.patch("apps.sites.consumers.timezone.now") as mock_now,
        ):
            mock_now.return_value.timestamp.side_effect = [
                1000.0,
                1000.1,
                1000.2,
                1001.2,
            ]
            self.assertTrue(consumer._should_emit_presence(event="join", staff=True))
            self.assertFalse(consumer._should_emit_presence(event="leave", staff=True))
            self.assertFalse(consumer._should_emit_presence(event="join", staff=True))
            self.assertTrue(consumer._should_emit_presence(event="join", staff=True))

    def test_should_emit_presence_tracks_staff_and_visitor_channels_independently(self):
        """Staff flap suppression should not hide visitor presence announcements."""

        consumer = self._build_consumer(session_pk=102)

        with (
            mock.patch(
                "apps.sites.consumers.settings.PAGES_CHAT_PRESENCE_FLAP_WINDOW_SECONDS",
                1,
                create=True,
            ),
            mock.patch("apps.sites.consumers.timezone.now") as mock_now,
        ):
            mock_now.return_value.timestamp.side_effect = [1000.0, 1000.1, 1000.2]
            self.assertTrue(consumer._should_emit_presence(event="join", staff=True))
            self.assertFalse(consumer._should_emit_presence(event="leave", staff=True))
            self.assertTrue(consumer._should_emit_presence(event="join", staff=False))

    def test_presence_flap_window_defaults_to_module_fallback(self):
        """Default flap window should stay stable when setting is absent."""

        consumer = self._build_consumer(session_pk=103)

        with mock.patch.dict(
            "apps.sites.consumers.settings.__dict__",
            {},
            clear=False,
        ):
            self.assertEqual(
                consumer._presence_flap_window_seconds(),
                DEFAULT_PRESENCE_FLAP_WINDOW_SECONDS,
            )


class ChatConsumerAccessControlTests(TestCase):
    """Coverage for server-side chat access control checks."""

    def test_chat_access_allowed_when_site_public_chat_enabled(self):
        """Visitors should be able to connect when site-level public chat is enabled."""

        consumer = ChatConsumer()

        with (
            mock.patch("apps.sites.consumers.settings.PAGES_CHAT_ENABLED", True),
            mock.patch(
                "apps.sites.consumers.is_suite_feature_enabled", return_value=True
            ),
            mock.patch.object(
                consumer,
                "_current_site",
                return_value=SimpleNamespace(
                    profile=SimpleNamespace(enable_public_chat=True)
                ),
            ),
        ):
            allowed = consumer._is_chat_access_allowed_sync(user=SimpleNamespace())

        self.assertTrue(allowed)

    def test_current_site_prefers_websocket_host_header(self):
        """Site resolution should follow the websocket host, not only the default site."""

        consumer = ChatConsumer()
        consumer.scope = {"headers": [(b"host", b"chat.example.test:8443")]}
        resolved_site = SimpleNamespace(domain="chat.example.test")
        queryset = SimpleNamespace(
            filter=lambda **_kwargs: SimpleNamespace(first=lambda: resolved_site)
        )

        with (
            mock.patch(
                "apps.sites.consumers.Site.objects.select_related",
                return_value=queryset,
            ) as mock_select_related,
            mock.patch(
                "apps.sites.consumers.Site.objects.get_current"
            ) as mock_get_current,
        ):
            site = consumer._current_site()

        self.assertIs(site, resolved_site)
        mock_select_related.assert_called_once_with("profile")
        mock_get_current.assert_not_called()
