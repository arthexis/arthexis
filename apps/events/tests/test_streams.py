from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from redis.exceptions import ConnectionError

from apps.events.streams import DEFAULT_EVENT_STREAM_MAXLEN, EVENT_STREAM, emit_event


class EventStreamTests(SimpleTestCase):
    @override_settings(EVENTS_REDIS_URL="redis://localhost:6379/0")
    @patch("apps.events.streams._redis_client")
    def test_emit_event_appends_typed_event(self, redis_client):
        client = redis_client.return_value
        client.xadd.return_value = "1-0"

        event_id = emit_event(
            "ocpp.authorization",
            charger_id="gway-001",
            status="Accepted",
        )

        self.assertEqual(event_id, "1-0")
        stream, event = client.xadd.call_args.args
        self.assertEqual(stream, EVENT_STREAM)
        self.assertEqual(event["type"], "ocpp.authorization")
        self.assertEqual(event["charger_id"], "gway-001")
        self.assertEqual(event["status"], "Accepted")
        self.assertIn("timestamp", event)
        self.assertEqual(
            client.xadd.call_args.kwargs["maxlen"], DEFAULT_EVENT_STREAM_MAXLEN
        )
        self.assertTrue(client.xadd.call_args.kwargs["approximate"])

    @override_settings(
        EVENTS_REDIS_URL="redis://localhost:6379/0",
        EVENTS_STREAM_MAXLEN=0,
    )
    @patch("apps.events.streams._redis_client")
    def test_stream_retention_can_be_unbounded(self, redis_client):
        client = redis_client.return_value
        client.xadd.return_value = "1-0"

        emit_event("ocpp.authorization", status="Accepted")

        self.assertEqual(client.xadd.call_args.kwargs, {})

    @override_settings(EVENTS_REDIS_URL="redis://localhost:6379/0")
    @patch("apps.events.streams._redis_client")
    def test_emit_event_is_non_fatal_when_redis_is_unavailable(self, redis_client):
        redis_client.return_value.xadd.side_effect = ConnectionError("offline")

        self.assertIsNone(emit_event("ocpp.authorization", status="Rejected"))
