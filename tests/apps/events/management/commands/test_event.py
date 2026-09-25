from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.events.models import EventEnvelope

pytestmark = pytest.mark.django_db


def test_publish_creates_event_with_default_producer() -> None:
    output = StringIO()

    call_command(
        "event",
        "publish",
        "test.event",
        "--payload",
        '{"value": 1}',
        stdout=output,
    )

    envelope = EventEnvelope.objects.get()
    assert envelope.event_type == "test.event"
    assert envelope.producer == "command"
    assert envelope.payload == {"value": 1}
    assert output.getvalue().strip() == str(envelope.event_id)


def test_pub_alias_accepts_explicit_producer() -> None:
    output = StringIO()

    call_command(
        "event",
        "pub",
        "test.event",
        "--producer",
        "tests",
        stdout=output,
    )

    assert EventEnvelope.objects.get().producer == "tests"


@pytest.mark.parametrize("payload", ["{not-json}", '["value"]', '"value"', "1", "null"])
def test_command_rejects_invalid_or_non_object_json(payload: str) -> None:
    with pytest.raises(CommandError, match="Payload must be a JSON object."):
        call_command(
            "event",
            "publish",
            "test.event",
            "--payload",
            payload,
        )

    assert not EventEnvelope.objects.exists()
