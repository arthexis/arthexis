from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.nodes.models import NetMessage, Node, NodeRole


@pytest.mark.django_db
def test_lcd_reset_rebuilds_active_messages(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    settings.DATABASES["default"]["NAME"] = ":memory:"
    settings.DATABASES["default"].setdefault("TEST", {})["NAME"] = ":memory:"

    role = NodeRole.objects.create(name="Control", acronym="CTRL")

    monkeypatch.setattr(Node, "get_current_mac", classmethod(lambda cls: "00:11:22:33:44:55"))
    Node._local_cache.clear()

    local_node = Node.objects.create(
        hostname="local",
        network_hostname="local",
        address="",
        ipv4_address="",
        mac_address="00:11:22:33:44:55",
        public_endpoint="local-endpoint",
        role=role,
        current_relation=Node.Relation.SELF,
    )

    other_node = Node.objects.create(
        hostname="other",
        network_hostname="other",
        address="",
        ipv4_address="",
        mac_address="00:11:22:33:44:66",
        public_endpoint="other-endpoint",
    )

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-high").write_text("old\nstale\n", encoding="utf-8")
    (lock_dir / "service.lck").write_text("demo", encoding="utf-8")

    expires_at = timezone.now() + timedelta(hours=1)
    NetMessage.objects.create(
        subject="Hello",
        body="World",
        node_origin=local_node,
        filter_node=local_node,
        expires_at=expires_at,
        lcd_channel_type="high",
    )
    NetMessage.objects.create(
        subject="Skip",
        body="Me",
        node_origin=other_node,
        filter_node=other_node,
        expires_at=expires_at,
        lcd_channel_type="low",
    )

    monkeypatch.setattr("shutil.which", lambda _: None)

    call_command("lcd_reset", "--skip-restart")

    payload = (lock_dir / "lcd-high").read_text(encoding="utf-8").splitlines()
    assert payload[0] == "Hello"
    assert payload[1] == "World"
    assert not (lock_dir / "lcd-low").exists()
