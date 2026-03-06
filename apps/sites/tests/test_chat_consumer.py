import time
from types import SimpleNamespace
from unittest import TestCase, mock

from django.core.cache import cache

from apps.sites.consumers import ChatConsumer


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

        with mock.patch("apps.sites.consumers.PRESENCE_FLAP_WINDOW_SECONDS", 1):
            self.assertTrue(consumer._should_emit_presence(event="join", staff=True))
            self.assertFalse(consumer._should_emit_presence(event="leave", staff=True))
            self.assertFalse(consumer._should_emit_presence(event="join", staff=True))

            time.sleep(1.1)

            self.assertTrue(consumer._should_emit_presence(event="join", staff=True))

    def test_should_emit_presence_tracks_staff_and_visitor_channels_independently(self):
        """Staff flap suppression should not hide visitor presence announcements."""

        consumer = self._build_consumer(session_pk=102)

        with mock.patch("apps.sites.consumers.PRESENCE_FLAP_WINDOW_SECONDS", 1):
            self.assertTrue(consumer._should_emit_presence(event="join", staff=True))
            self.assertFalse(consumer._should_emit_presence(event="leave", staff=True))
            self.assertTrue(consumer._should_emit_presence(event="join", staff=False))
