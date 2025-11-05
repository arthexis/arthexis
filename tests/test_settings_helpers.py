import pathlib

import pytest

from config import settings_helpers


def _original_validate(host, allowed_hosts):
    return host in allowed_hosts


class TestValidateHostWithSubnets:
    def test_accepts_ipv4_within_allowed_subnet(self):
        allowed = ["example.com", "192.168.1.0/24"]

        assert settings_helpers.validate_host_with_subnets(
            "192.168.1.42",
            allowed,
            original_validate=_original_validate,
        )

    def test_accepts_ipv6_literal_with_port(self):
        allowed = ["2001:db8::/32"]

        assert settings_helpers.validate_host_with_subnets(
            "[2001:db8::1]:8443",
            allowed,
            original_validate=_original_validate,
        )

    def test_falls_back_to_original_validator(self):
        allowed = ["example.com", "10.0.0.0/24"]

        assert not settings_helpers.validate_host_with_subnets(
            "10.1.0.5",
            allowed,
            original_validate=_original_validate,
        )


class TestLoadSecretKey:
    def test_prefers_environment_variables(self, tmp_path: pathlib.Path):
        env = {"DJANGO_SECRET_KEY": "env-secret"}

        result = settings_helpers.load_secret_key(tmp_path, env=env)

        assert result == "env-secret"

    def test_reads_existing_secret_file(self, tmp_path: pathlib.Path):
        secret_file = tmp_path / "locks" / "django-secret.key"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text("stored-secret", encoding="utf-8")

        result = settings_helpers.load_secret_key(tmp_path, env={})

        assert result == "stored-secret"

    def test_generates_and_persists_secret(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        generated = "generated-secret"
        monkeypatch.setattr(
            settings_helpers,
            "get_random_secret_key",
            lambda: generated,
        )

        result = settings_helpers.load_secret_key(tmp_path, env={})

        secret_file = tmp_path / "locks" / "django-secret.key"
        assert result == generated
        assert secret_file.read_text(encoding="utf-8") == generated

    def test_regenerates_when_secret_file_blank(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        secret_file = tmp_path / "locks" / "django-secret.key"
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text("\n", encoding="utf-8")

        regenerated = "regenerated-secret"
        monkeypatch.setattr(
            settings_helpers,
            "get_random_secret_key",
            lambda: regenerated,
        )

        result = settings_helpers.load_secret_key(tmp_path, env={})

        assert result == regenerated
        assert secret_file.read_text(encoding="utf-8") == regenerated


def test_discover_local_ip_addresses_collects_hostname_ips(monkeypatch):
    monkeypatch.setattr(settings_helpers.socket, "gethostname", lambda: "example-host")
    monkeypatch.setattr(settings_helpers.socket, "getfqdn", lambda: "example-host.local")
    monkeypatch.setattr(
        settings_helpers.socket,
        "gethostbyname_ex",
        lambda host: (host, ["alias"], ["203.0.113.10", "127.0.0.1"]),
    )
    monkeypatch.setattr(
        settings_helpers.socket,
        "getaddrinfo",
        lambda host, *_: [
            (None, None, None, None, ("2001:db8::1", 0)),
            (None, None, None, None, ("fe80::1%eth0", 0)),
        ],
    )
    monkeypatch.setattr(settings_helpers, "_iter_ip_addr_show", lambda: ["10.0.0.5"])
    monkeypatch.setattr(
        settings_helpers,
        "_iter_command_addresses",
        lambda command: ["198.51.100.7"],
    )
    monkeypatch.setattr(settings_helpers, "_iter_metadata_addresses", lambda env: [])

    addresses = settings_helpers.discover_local_ip_addresses(env={"DISABLE_METADATA_IP_DISCOVERY": "1"})

    assert "203.0.113.10" in addresses
    assert "2001:db8::1" in addresses
    assert "fe80::1" in addresses
    assert "10.0.0.5" in addresses
    assert "198.51.100.7" in addresses
    # Loopback address must still be present even if not returned by the mocks.
    assert "127.0.0.1" in addresses


def test_discover_local_ip_addresses_reads_metadata(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"54.161.177.151\n2001:db8::2"

    monkeypatch.setattr(settings_helpers, "_iter_ip_addr_show", lambda: [])
    monkeypatch.setattr(settings_helpers, "_iter_command_addresses", lambda command: [])
    monkeypatch.setattr(settings_helpers.socket, "gethostname", lambda: "")
    monkeypatch.setattr(settings_helpers.socket, "getfqdn", lambda: "")
    monkeypatch.setattr(settings_helpers.socket, "gethostbyname_ex", lambda host: (host, [], []))
    monkeypatch.setattr(settings_helpers.socket, "getaddrinfo", lambda host, *_: [])
    monkeypatch.setattr(settings_helpers.urllib.request, "urlopen", lambda url, timeout=0.5: _FakeResponse())

    addresses = settings_helpers.discover_local_ip_addresses(env={})

    assert "54.161.177.151" in addresses
    assert "2001:db8::2" in addresses


@pytest.mark.parametrize(
    "candidate, expected",
    [
        ("", None),
        ("invalid", None),
        ("0.0.0.0", "0.0.0.0"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1%eth0", "2001:db8::1"),
    ],
)
def test_normalize_candidate_ip(candidate, expected):
    result = settings_helpers._normalize_candidate_ip(candidate)
    assert result == expected
