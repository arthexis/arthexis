import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

import pytest
from django.core.management import call_command

from nodes.models import NetMessage


@pytest.mark.django_db
def test_purge_net_messages_command_deletes_messages():
    initial = NetMessage.objects.count()
    NetMessage.objects.create(subject="subject", body="body")
    NetMessage.objects.create(subject="second", body="more")

    assert NetMessage.objects.count() == initial + 2

    call_command("purge_net_messages")

    assert NetMessage.objects.count() == 0
