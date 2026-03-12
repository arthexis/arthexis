"""Regression coverage for runtime service probing helpers."""

from __future__ import annotations

import pytest

from utils import service_probe


@pytest.mark.pr(6201)
def test_parse_runserver_port_prefers_valid_addrport() -> None:
    """Port parsing should extract a valid address-port argument from runserver commands."""

    command = "1234 python manage.py runserver 0.0.0.0:9010 --noreload"
    assert service_probe.parse_runserver_port(command) == 9010


@pytest.mark.pr(6201)
def test_parse_runserver_port_supports_standalone_numeric_port() -> None:
    """Port parsing should accept valid standalone numeric runserver addrport values."""

    command = "4321 python manage.py runserver 9000 --noreload"
    assert service_probe.parse_runserver_port(command) == 9000


@pytest.mark.pr(6201)
def test_probe_admin_login_requires_http_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reachability should depend on an HTTP response rather than only opening a TCP socket."""

    class FakeResponse:
        status = 400

        def read(self) -> bytes:
            return b""

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert host == "127.0.0.1"
            assert port == 8888
            assert timeout == 1.0

        def request(self, method: str, path: str) -> None:
            assert method == "GET"
            assert path == "/admin/login/"

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(service_probe.http.client, "HTTPConnection", FakeConnection)

    result = service_probe.probe_admin_login(8888)
    assert result.reachable is True
    assert result.status_code == 400


@pytest.mark.pr(6201)
def test_probe_admin_login_connection_error_returns_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP probe errors should produce an unreachable result."""

    class BrokenConnection:
        def __init__(self, *_args, **_kwargs) -> None:
            raise OSError("connection refused")

    monkeypatch.setattr(service_probe.http.client, "HTTPConnection", BrokenConnection)

    result = service_probe.probe_admin_login(8888)
    assert result.reachable is False
    assert result.status_code is None
