from unittest.mock import Mock

import pytest

from apps.events.registry import dispatch_to_subscribers, subscribe

pytestmark = pytest.mark.django_db


def test_registered_subscribers_receive_matching_event(event_factory) -> None:
    handler = Mock()
    subscribe("test.matching", handler)
    envelope = event_factory(event_type="test.matching", payload={})

    count = dispatch_to_subscribers(envelope)

    assert count == 1
    handler.assert_called_once_with(envelope)


def test_subscription_is_idempotent(event_factory) -> None:
    handler = Mock()
    subscribe("test.idempotent", handler)
    subscribe("test.idempotent", handler)
    envelope = event_factory(event_type="test.idempotent", payload={})

    assert dispatch_to_subscribers(envelope) == 1
