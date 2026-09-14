from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.events.queues import publish_queue_event


class QueueEventTests(SimpleTestCase):
    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    @patch("apps.events.queues.Connection")
    def test_publish_queue_event_sends_plain_mapping(self, connection_class):
        connection = Mock()
        connection_class.return_value.__enter__.return_value = connection
        queue = Mock()
        connection.SimpleQueue.return_value.__enter__.return_value = queue

        published = publish_queue_event(
            "ocpp.authorization",
            "ocpp.authorization",
            charger_id="gway-001",
            id_tag="04A1B2C3",
            status="Accepted",
            unused=None,
        )

        self.assertTrue(published)
        connection_class.assert_called_once_with(
            "redis://localhost:6379/0", connect_timeout=1
        )
        connection.ensure_connection.assert_called_once_with(max_retries=0, timeout=1)
        connection.SimpleQueue.assert_called_once_with("ocpp.authorization")
        queue.put.assert_called_once()
        event = queue.put.call_args.args[0]
        self.assertEqual(event["type"], "ocpp.authorization")
        self.assertEqual(event["charger_id"], "gway-001")
        self.assertEqual(event["id_tag"], "04A1B2C3")
        self.assertEqual(event["status"], "Accepted")
        self.assertIn("timestamp", event)
        self.assertNotIn("unused", event)
        self.assertEqual(queue.put.call_args.kwargs, {"serializer": "json"})

    @override_settings(CELERY_BROKER_URL="")
    @patch("apps.events.queues.Connection")
    def test_publish_queue_event_skips_when_broker_is_unconfigured(
        self, connection_class
    ):
        self.assertFalse(
            publish_queue_event("ocpp.authorization", "ocpp.authorization")
        )
        connection_class.assert_not_called()

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    @patch("apps.events.queues.Connection", side_effect=OSError("broker unavailable"))
    def test_publish_queue_event_does_not_raise_on_broker_failure(self, _connection):
        self.assertFalse(
            publish_queue_event("ocpp.authorization", "ocpp.authorization")
        )

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    @patch("apps.events.queues.Connection")
    def test_publish_queue_event_does_not_retry_connection_failures(self, connection_class):
        connection = Mock()
        connection_class.return_value.__enter__.return_value = connection
        connection.ensure_connection.side_effect = OSError("broker unavailable")

        self.assertFalse(
            publish_queue_event("ocpp.authorization", "ocpp.authorization")
        )
        connection.ensure_connection.assert_called_once_with(max_retries=0, timeout=1)
        connection.SimpleQueue.assert_not_called()
