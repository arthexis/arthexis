import io
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.nginx import config_utils
from apps.nodes.models import Node


@pytest.mark.django_db
def test_check_forwarders_reports_external_websockets(monkeypatch):
    node = Node.objects.create(
        hostname="forwarder-node",
        public_endpoint="forwarder-node",
        port=8888,
    )

    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    websocket_content = "\n".join(config_utils.websocket_directives())
    monkeypatch.setattr(
        "apps.ocpp.management.commands.check_forwarders._build_nginx_report",
        lambda: {
            "mode": "public",
            "port": 8888,
            "actual_path": Path("/etc/nginx/sites-enabled/arthexis.conf"),
            "actual_content": websocket_content,
            "differs": False,
            "expected_error": "",
            "actual_error": "",
            "external_websockets": True,
        },
    )

    stream = io.StringIO()
    call_command("check_forwarders", stdout=stream)
    output = stream.getvalue()

    assert "External websockets enabled: True" in output
    assert "External websocket config: True" in output
