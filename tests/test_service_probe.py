"""Regression coverage for runtime service probing helpers."""

from __future__ import annotations

import pytest

from utils import service_probe

@pytest.mark.pr(6201)
def test_parse_runserver_port_supports_addrport_option_with_value() -> None:
    """Port parsing should support ``--addrport`` when passed as a separate token."""

    command = "4321 python manage.py runserver --addrport 9100 --noreload"
    assert service_probe.parse_runserver_port(command) == 9100


@pytest.mark.pr(6201)
def test_probe_admin_login_requires_http_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reachability should depend on an HTTP response rather than only opening a TCP socket."""

    class FakeResponse:
        """Minimal HTTP response stub with a fixed integer status code."""

        status = 400

        def read(self) -> bytes:
            """Return an empty body payload.

            Returns:
                bytes: Empty bytes content.
            """

            return b""

    class FakeConnection:
        """HTTPConnection stub validating probe request arguments.

        Args:
            host: Expected hostname for the request target.
            port: Expected request port.
            timeout: Expected request timeout in seconds.
        """

        def __init__(self, host: str, port: int, timeout: float) -> None:
            """Initialize the stub and assert expected constructor arguments.

            Args:
                host: Connection host.
                port: Connection port.
                timeout: Connection timeout.

            Returns:
                None.

            Raises:
                AssertionError: If expected constructor arguments differ.
            """

            assert host == "127.0.0.1"
            assert port == 8888
            assert timeout == 1.0

        def request(self, method: str, path: str) -> None:
            """Assert request method and path.

            Args:
                method: HTTP method name.
                path: HTTP path being requested.

            Returns:
                None.

            Raises:
                AssertionError: If probe uses unexpected request values.
            """

            assert method == "GET"
            assert path == "/admin/login/"

        def getresponse(self) -> FakeResponse:
            """Return the fake HTTP response object.

            Returns:
                FakeResponse: Stub response with status metadata.
            """

            return FakeResponse()

        def close(self) -> None:
            """Close the fake connection.

            Returns:
                None.
            """

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
        """HTTPConnection stub that raises to simulate probe failures."""

        def __init__(self, *_args, **_kwargs) -> None:
            """Raise an OS-level connection error on construction.

            Returns:
                None.

            Raises:
                OSError: Always raised to emulate connection refusal.
            """

            raise OSError("connection refused")

        def request(self, _method: str, _path: str) -> None:
            """Unused request hook kept for API completeness.

            Returns:
                None.
            """

            return None

        def getresponse(self) -> object:
            """Unused response hook kept for API completeness.

            Returns:
                object: Never returned in this stub.
            """

            return object()

        def close(self) -> None:
            """Unused close hook kept for API completeness.

            Returns:
                None.
            """

            return None

    monkeypatch.setattr(service_probe.http.client, "HTTPConnection", BrokenConnection)

    result = service_probe.probe_admin_login(8888)
    assert result.reachable is False
    assert result.status_code is None
