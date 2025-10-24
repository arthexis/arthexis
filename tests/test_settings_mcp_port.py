import pytest
from django.core.exceptions import ImproperlyConfigured

from config import settings


def test_env_int_allows_prefixed_port_with_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_SIGIL_PORT", "MCP_8888/")

    value = settings._env_int("MCP_SIGIL_PORT", 8800, allow_mcp_prefix=True)

    assert value == 8888


def test_env_int_strips_repeated_prefixes_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_SIGIL_PORT", "  MCP_MCP_9000// ")

    value = settings._env_int("MCP_SIGIL_PORT", 8800, allow_mcp_prefix=True)

    assert value == 9000


def test_env_int_rejects_non_numeric_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_SIGIL_PORT", "MCP_invalid")

    with pytest.raises(ImproperlyConfigured):
        settings._env_int("MCP_SIGIL_PORT", 8800, allow_mcp_prefix=True)


def test_env_int_handles_trailing_slash_without_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTHER_PORT", "1234/")

    value = settings._env_int("OTHER_PORT", 8800)

    assert value == 1234
