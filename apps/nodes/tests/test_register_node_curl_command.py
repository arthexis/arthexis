import io

import pytest
from django.core.management import get_commands, load_command_class
from django.core.management.base import CommandError


def _load_command():
    app_name = get_commands()["register-node-curl"]
    return load_command_class(app_name, "register-node-curl")


def test_register_node_curl_command_outputs_script():
    command = _load_command()
    command.stdout = io.StringIO()

    command.handle(upstream="example.com", local_base="https://local:8888", token="abc123")

    output = command.stdout.getvalue()
    assert 'TOKEN="abc123"' in output
    assert 'UPSTREAM_INFO="https://example.com/nodes/info/"' in output
    assert 'UPSTREAM_REGISTER="https://example.com/nodes/register/"' in output
    assert 'LOCAL_INFO="https://local:8888/nodes/info/"' in output
    assert 'LOCAL_REGISTER="https://local:8888/nodes/register/"' in output
    assert "RELATION=\"Downstream\"" in output
    assert "RELATION=\"Upstream\"" in output


def test_register_node_curl_command_rejects_invalid_scheme():
    command = _load_command()

    with pytest.raises(CommandError, match="Upstream base URL must use https"):
        command.handle(upstream="http://example.com", local_base="https://local:8888", token="abc123")
