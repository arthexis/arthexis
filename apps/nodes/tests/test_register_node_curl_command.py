import io

import pytest
from django.core.management import get_commands, load_command_class
from django.core.management.base import CommandError


def _load_node_command():
    app_name = get_commands()["node"]
    return load_command_class(app_name, "node")


def test_node_register_curl_outputs_script():
    command = _load_node_command()
    command.stdout = io.StringIO()

    command.handle(
        action="register_curl",
        upstream="example.com",
        local_base="https://local:8888",
        token="abc123",
    )

    output = command.stdout.getvalue()
    assert 'TOKEN="abc123"' in output
    assert 'UPSTREAM_INFO="https://example.com/nodes/info/"' in output
    assert 'UPSTREAM_REGISTER="https://example.com/nodes/register/"' in output
    assert 'LOCAL_INFO="https://local:8888/nodes/info/"' in output
    assert 'LOCAL_REGISTER="https://local:8888/nodes/register/"' in output
    assert "RELATION=\"Downstream\"" in output
    assert "RELATION=\"Upstream\"" in output


def test_node_register_curl_rejects_invalid_scheme():
    command = _load_node_command()

    with pytest.raises(CommandError, match="Upstream base URL must use https"):
        command.handle(
            action="register_curl",
            upstream="http://example.com",
            local_base="https://local:8888",
            token="abc123",
        )


def test_node_register_curl_rejects_invalid_token():
    command = _load_node_command()

    with pytest.raises(
        CommandError,
        match="Token must contain only alphanumeric characters, hyphens, or underscores.",
    ):
        command.handle(
            action="register_curl",
            upstream="https://example.com",
            local_base="https://local:8888",
            token="bad token!",
        )


def test_node_register_curl_rejects_base_url_with_path():
    command = _load_node_command()

    with pytest.raises(CommandError, match="Upstream base URL must not include a path"):
        command.handle(
            action="register_curl",
            upstream="https://example.com/bad",
            local_base="https://local:8888",
            token="abc123",
        )


def test_node_register_curl_rejects_base_url_with_query():
    command = _load_node_command()

    with pytest.raises(CommandError, match="Local base URL must not include credentials, query params, or fragments"):
        command.handle(
            action="register_curl",
            upstream="https://example.com",
            local_base="https://local:8888?x=1",
            token="abc123",
        )
