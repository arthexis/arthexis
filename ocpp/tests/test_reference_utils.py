import pytest

from ocpp import reference_utils


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", True),
        ("[127.0.0.1]", True),
        ("::1", False),
        ("[::1]", False),
        ("localhost", False),
        ("", False),
        (None, False),
        ("not a host", False),
    ],
)
def test_host_is_local_loopback(host, expected):
    assert reference_utils.host_is_local_loopback(host) is expected


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://127.0.0.1", True),
        ("https://localhost", False),
        ("https://[::1]", False),
        ("", False),
        (None, False),
    ],
)
def test_url_targets_local_loopback(url, expected):
    assert reference_utils.url_targets_local_loopback(url) is expected


@pytest.mark.parametrize(
    "url, expected_host",
    [
        ("https://user:pass@127.0.0.1", "127.0.0.1"),
        ("https://[::1]", "::1"),
        ("https://user:pass@[::1]:8443/path", "::1"),
    ],
)
def test_url_targets_local_loopback_delegates_to_host_check(monkeypatch, url, expected_host):
    sentinel = object()

    called_with = {}

    def fake_host_is_local_loopback(host):
        called_with["host"] = host
        return sentinel

    monkeypatch.setattr(reference_utils, "host_is_local_loopback", fake_host_is_local_loopback)

    result = reference_utils.url_targets_local_loopback(url)

    assert called_with["host"] == expected_host
    assert result is sentinel
