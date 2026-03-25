from __future__ import annotations

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test.utils import override_settings
from django.utils import timezone

from apps.prototypes.models import Prototype
from apps.prototypes.prototype_ops import RETIREMENT_MESSAGE

@pytest.mark.django_db
def test_prototype_status_lists_retired_metadata_records():
    Prototype.objects.create(
        slug="vision_lab",
        name="Vision Lab",
        retired_at=timezone.now(),
        retirement_notes="Archived during scaffold retirement.",
    )
    stdout = io.StringIO()

    call_command("prototype", "status", stdout=stdout)

    output = stdout.getvalue()
    assert RETIREMENT_MESSAGE in output
    assert "vision_lab" in output
    assert "runnable=False" in output

