from __future__ import annotations

import pytest
from django.utils import timezone

from apps.prototypes import prototype_ops
from apps.prototypes.models import Prototype


def test_retirement_message_mentions_metadata_only_mode():
    assert "metadata only" in prototype_ops.RETIREMENT_MESSAGE


@pytest.mark.django_db
def test_retire_prototype_marks_record_inert():
    prototype = Prototype.objects.create(slug="vision_lab", name="Vision Lab")

    returned = prototype_ops.retire_prototype(prototype, note="Archived after workflow removal.")

    prototype.refresh_from_db()
    assert returned.pk == prototype.pk
    assert prototype.is_active is False
    assert prototype.is_runnable is False
    assert prototype.retired_at is not None
    assert prototype.retirement_notes == "Archived after workflow removal."


@pytest.mark.django_db
def test_prototype_clean_forces_non_runnable_state():
    prototype = Prototype(
        slug="audio_lab",
        name="Audio Lab",
        is_active=True,
        is_runnable=True,
        retired_at=timezone.now(),
    )

    prototype.full_clean()

    assert prototype.is_active is False
    assert prototype.is_runnable is False
