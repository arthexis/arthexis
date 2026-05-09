from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.nodes.models import Node
from apps.ocpp.models import CPForwarder


@pytest.mark.django_db
def test_track_cp_forward_enable_forwarder_via_cli() -> None:
    target = Node.objects.create(hostname="peer-forward-node")
    forwarder = CPForwarder.objects.create(target_node=target, enabled=False)

    out = StringIO()
    call_command("track_cp_forward", "--enable-forwarder", str(forwarder.pk), stdout=out)

    forwarder.refresh_from_db()
    assert forwarder.enabled is True
    assert f"Enabled CP forwarder #{forwarder.pk}" in out.getvalue()


@pytest.mark.django_db
def test_track_cp_forward_enable_forwarder_missing_id_raises() -> None:
    with pytest.raises(CommandError, match="was not found"):
        call_command("track_cp_forward", "--enable-forwarder", "999999")
