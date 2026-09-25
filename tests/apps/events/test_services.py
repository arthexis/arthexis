import logging
from unittest.mock import patch

import pytest
from django.db import DatabaseError

from apps.events.models import EventEnvelope
from apps.events.services import publish, publish_safely

pytestmark = pytest.mark.django_db


def test_publish_persists_valid_event() -> None:
    envelope = publish(
        event_type="test.event",
        producer="tests",
        payload={"value": 1},
    )

    assert envelope.event_type == "test.event"
    assert envelope.producer == "tests"
    assert envelope.payload == {"value": 1}
    assert envelope.published_at is None
    assert EventEnvelope.objects.filter(pk=envelope.pk).exists()


def test_publish_copies_mapping_payload() -> None:
    payload = {"value": 1}

    envelope = publish(
        event_type="test.event",
        producer="tests",
        payload=payload,
    )
    payload["value"] = 2

    assert envelope.payload == {"value": 1}


@pytest.mark.parametrize(
    ("event_type", "producer", "message"),
    [
        ("", "tests", "Event type is required."),
        ("test.event", "", "Event producer is required."),
    ],
)
def test_publish_rejects_empty_required_fields(
    event_type: str,
    producer: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        publish(event_type=event_type, producer=producer, payload={})


def test_publish_rejects_non_json_serializable_payload() -> None:
    with pytest.raises(TypeError):
        publish(
            event_type="test.event",
            producer="tests",
            payload={"bad": object()},
        )

    assert not EventEnvelope.objects.exists()


def test_publish_safely_returns_persisted_event() -> None:
    envelope = publish_safely(
        event_type="test.event",
        producer="tests",
        payload={"value": 1},
    )

    assert envelope is not None
    assert envelope.payload == {"value": 1}


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("", {}),
        ("test.event", {"bad": object()}),
    ],
)
def test_publish_safely_contains_validation_and_serialization_failures(
    caplog,
    event_type: str,
    payload: dict[str, object],
) -> None:
    with caplog.at_level(logging.ERROR, logger="apps.events.services"):
        envelope = publish_safely(
            event_type=event_type,
            producer="tests",
            payload=payload,
        )

    assert envelope is None
    assert "Could not publish event" in caplog.text


def test_publish_safely_contains_database_failure(caplog) -> None:
    with patch("apps.events.services.publish", side_effect=DatabaseError("offline")):
        with caplog.at_level(logging.ERROR, logger="apps.events.services"):
            envelope = publish_safely(
                event_type="test.event",
                producer="tests",
                payload={},
            )

    assert envelope is None
    assert "Could not publish event" in caplog.text
