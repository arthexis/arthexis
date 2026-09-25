from datetime import timedelta

import pytest
from django.utils import timezone

from apps.events.models import EventEnvelope

pytestmark = pytest.mark.django_db


def test_event_defaults_are_durable_and_unpublished(event_factory) -> None:
    envelope = event_factory()

    assert envelope.event_id is not None
    assert envelope.created_at is not None
    assert envelope.published_at is None
    assert envelope.payload == {"value": 1}


def test_event_ids_are_unique(event_factory) -> None:
    first = event_factory(event_type="test.first")
    second = event_factory(event_type="test.second")

    assert first.event_id != second.event_id


def test_events_are_ordered_newest_first(event_factory) -> None:
    older = event_factory(event_type="test.older", payload={})
    event_factory(event_type="test.newer", payload={})
    EventEnvelope.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(hours=1)
    )

    assert list(EventEnvelope.objects.values_list("event_type", flat=True)) == [
        "test.newer",
        "test.older",
    ]
