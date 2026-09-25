from collections.abc import Callable

import pytest

from apps.events.models import EventEnvelope


@pytest.fixture
def event_factory() -> Callable[..., EventEnvelope]:
    def create_event(**overrides) -> EventEnvelope:
        values = {
            "event_type": "test.event",
            "producer": "tests",
            "payload": {"value": 1},
        }
        values.update(overrides)
        return EventEnvelope.objects.create(**values)

    return create_event
