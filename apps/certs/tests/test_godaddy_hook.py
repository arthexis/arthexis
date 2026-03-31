from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


HOOK_PATH = Path(__file__).resolve().parents[3] / "scripts" / "certbot" / "godaddy_hook.py"
SPEC = importlib.util.spec_from_file_location("godaddy_hook", HOOK_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_zone_and_name_validates_zone_override_suffix():
    with pytest.raises(RuntimeError, match="GODADDY_ZONE"):
        MODULE._zone_and_name("_acme-challenge.example.com", "other.com")


def test_emit_log_writes_to_configured_log_file(tmp_path, capsys, monkeypatch):
    log_path = tmp_path / "hook.log"
    monkeypatch.setattr(MODULE, "HOOK_LOG_PATH", str(log_path))

    MODULE._emit_log("diagnostic-message")

    captured = capsys.readouterr()
    assert "diagnostic-message" in captured.out
    assert "diagnostic-message" in log_path.read_text(encoding="utf-8")


def test_zone_and_name_derives_zone_without_override(capsys):
    zone, host = MODULE._zone_and_name("_acme-challenge.example.com")

    captured = capsys.readouterr()
    assert zone == "example.com"
    assert host == "_acme-challenge"
    assert "derived zone 'example.com'" in captured.out


def test_wait_for_dns_txt_propagation_raises_timeout(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 1, 2, 2]).__next__)
    monkeypatch.setattr(
        MODULE,
        "_query_authoritative_txt_values",
        lambda *_args, **_kwargs: ({"ns1.example.net": {"192.0.2.10": {"stale-value"}}}, []),
    )

    with pytest.raises(TimeoutError, match="DNS propagation timeout"):
        MODULE._wait_for_dns_txt_propagation(
            zone="example.com",
            challenge_domain="_acme-challenge.example.com",
            expected_value="expected-value",
            timeout_seconds=1,
        )


def test_fetch_existing_txt_values_returns_empty_for_404(monkeypatch):
    class Response:
        status_code = 404
        text = "not found"

    monkeypatch.setattr(MODULE, "_godaddy_request", lambda *_args, **_kwargs: Response())

    assert MODULE._fetch_existing_txt_values("example.com", "_acme-challenge") == []


def test_query_authoritative_txt_values_ignores_nxdomain(monkeypatch):
    class Answer:
        def __init__(self, value):
            self.target = value

        def __str__(self):
            return self.target

    class Resolver:
        def __init__(self, configure=True):
            self.configure = configure
            self.nameservers = []
            self.lifetime = 0

        def resolve(self, name, rdtype):
            if rdtype == "NS":
                return [Answer("ns1.example.net.")]
            if name == "ns1.example.net" and rdtype == "A":
                return [Answer("192.0.2.10")]
            if name == "ns1.example.net" and rdtype == "AAAA":
                raise MODULE.dns.resolver.NoAnswer
            if rdtype == "TXT":
                raise MODULE.dns.resolver.NXDOMAIN
            raise AssertionError(f"Unexpected query {name} {rdtype}")

    monkeypatch.setattr(MODULE.dns.resolver, "Resolver", Resolver)

    observed_by_ns, errors = MODULE._query_authoritative_txt_values(
        "example.com", "_acme-challenge.example.com"
    )

    assert observed_by_ns == {"ns1.example.net": {"192.0.2.10": set()}}
    assert errors == []


def test_query_authoritative_txt_values_does_not_repeat_a_lookup_when_aaaa_missing(
    monkeypatch,
):
    class Answer:
        def __init__(self, value):
            self.target = value

        def __str__(self):
            return self.target

    queries: list[tuple[bool, str, str]] = []

    class Resolver:
        def __init__(self, configure=True):
            self.configure = configure
            self.nameservers = []
            self.lifetime = 0

        def resolve(self, name, rdtype):
            queries.append((self.configure, name, rdtype))
            if rdtype == "NS":
                return [Answer("ns1.example.net.")]
            if name == "ns1.example.net" and rdtype == "A":
                return [Answer("192.0.2.10")]
            if name == "ns1.example.net" and rdtype == "AAAA":
                raise MODULE.dns.resolver.NoAnswer
            if rdtype == "TXT":
                raise MODULE.dns.resolver.NXDOMAIN
            raise AssertionError(f"Unexpected query {name} {rdtype}")

    monkeypatch.setattr(MODULE.dns.resolver, "Resolver", Resolver)

    observed_by_ns, _errors = MODULE._query_authoritative_txt_values(
        "example.com", "_acme-challenge.example.com"
    )

    assert observed_by_ns == {"ns1.example.net": {"192.0.2.10": set()}}
    assert queries.count((False, "ns1.example.net", "A")) == 1


def test_wait_for_dns_txt_propagation_requires_all_authoritative_nameservers(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 0, 0]).__next__)

    monkeypatch.setattr(
        MODULE,
        "_query_authoritative_txt_values",
        lambda *_args, **_kwargs: (
            {
                "ns1.example.net": {"192.0.2.10": {"expected-value"}},
                "ns2.example.net": {"192.0.2.11": {"stale-value"}},
            },
            [],
        ),
    )

    with pytest.raises(TimeoutError, match="missing nameservers"):
        MODULE._wait_for_dns_txt_propagation(
            zone="example.com",
            challenge_domain="_acme-challenge.example.com",
            expected_value="expected-value",
            timeout_seconds=0,
        )


def test_wait_for_dns_txt_propagation_requires_expected_value_on_every_ns_ip(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 0]).__next__)
    monkeypatch.setattr(
        MODULE,
        "_query_authoritative_txt_values",
        lambda *_args, **_kwargs: (
            {
                "ns1.example.net": {
                    "192.0.2.10": {"expected-value"},
                    "2001:db8::10": {"stale-value"},
                }
            },
            [],
        ),
    )

    with pytest.raises(TimeoutError, match="missing nameservers"):
        MODULE._wait_for_dns_txt_propagation(
            zone="example.com",
            challenge_domain="_acme-challenge.example.com",
            expected_value="expected-value",
            timeout_seconds=0,
        )


def test_query_authoritative_txt_values_uses_public_resolvers_for_ns_lookup(monkeypatch):
    class Answer:
        def __init__(self, value):
            self.target = value

        def __str__(self):
            return self.target

    seen_public_nameservers: list[str] = []

    class Resolver:
        def __init__(self, configure=True):
            self.configure = configure
            self.nameservers = []
            self.lifetime = 0

        def resolve(self, name, rdtype):
            if name == "example.com" and rdtype == "NS":
                if not self.nameservers:
                    raise AssertionError("Expected explicit public nameservers for NS lookup.")
                seen_public_nameservers.extend(self.nameservers)
                return [Answer("ns1.example.net.")]
            if name == "ns1.example.net" and rdtype == "A":
                return [Answer("192.0.2.10")]
            if name == "ns1.example.net" and rdtype == "AAAA":
                raise MODULE.dns.resolver.NoAnswer
            if rdtype == "TXT":
                raise MODULE.dns.resolver.NXDOMAIN
            raise AssertionError(f"Unexpected query {name} {rdtype}")

    monkeypatch.setattr(MODULE.dns.resolver, "Resolver", Resolver)

    MODULE._query_authoritative_txt_values("example.com", "_acme-challenge.example.com")

    assert seen_public_nameservers == list(MODULE.PUBLIC_DNS_RESOLVERS)


def test_query_authoritative_txt_values_queries_aaaa_when_a_has_noanswer(monkeypatch):
    class Answer:
        def __init__(self, value):
            self.target = value

        def __str__(self):
            return self.target

    class Resolver:
        def __init__(self, configure=True):
            self.configure = configure
            self.nameservers = []
            self.lifetime = 0

        def resolve(self, name, rdtype):
            if rdtype == "NS":
                return [Answer("ns1.example.net.")]
            if name == "ns1.example.net" and rdtype == "A":
                raise MODULE.dns.resolver.NoAnswer
            if name == "ns1.example.net" and rdtype == "AAAA":
                return [Answer("2001:db8::10")]
            if rdtype == "TXT":
                raise MODULE.dns.resolver.NoAnswer
            raise AssertionError(f"Unexpected query {name} {rdtype}")

    monkeypatch.setattr(MODULE.dns.resolver, "Resolver", Resolver)

    observed_by_ns, errors = MODULE._query_authoritative_txt_values(
        "example.com", "_acme-challenge.example.com"
    )

    assert errors == []
    assert observed_by_ns == {"ns1.example.net": {"2001:db8::10": set()}}


def test_query_authoritative_txt_values_raises_dns_nameserver_error_when_ns_missing(
    monkeypatch,
):
    class Resolver:
        def __init__(self, configure=True):
            self.configure = configure
            self.nameservers = []
            self.lifetime = 0

        def resolve(self, name, rdtype):
            if name == "example.com" and rdtype == "NS":
                return []
            raise AssertionError(f"Unexpected query {name} {rdtype}")

    monkeypatch.setattr(MODULE.dns.resolver, "Resolver", Resolver)

    with pytest.raises(MODULE.DNSNameserverError, match="No authoritative nameservers"):
        MODULE._query_authoritative_txt_values("example.com", "_acme-challenge.example.com")


def test_upsert_txt_record_replaces_existing_records(monkeypatch):
    calls: list[tuple[str, str, object]] = []

    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("CERTBOT_VALIDATION", "new-value")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.setenv("GODADDY_DNS_WAIT_SECONDS", "0")

    monkeypatch.setattr(MODULE, "_fetch_existing_txt_values", lambda *_args, **_kwargs: ["old"])

    class Response:
        status_code = 200
        text = "ok"

    def fake_request(method, path, *, payload=None):
        calls.append((method, path, payload))
        return Response()

    monkeypatch.setattr(MODULE, "_godaddy_request", fake_request)

    wait_calls: list[dict[str, object]] = []

    def fake_wait(**kwargs):
        wait_calls.append(kwargs)

    monkeypatch.setattr(MODULE, "_wait_for_dns_txt_propagation", fake_wait)
    monkeypatch.setattr(MODULE, "_wait_for_public_recursive_txt_propagation", fake_wait)

    MODULE._upsert_txt_record()

    assert calls == [
        (
            "PUT",
            "/v1/domains/example.com/records/TXT/_acme-challenge",
            [
                {"data": "new-value", "ttl": 600},
                {"data": "old", "ttl": 600},
            ],
        )
    ]
    assert wait_calls == []


def test_upsert_txt_record_uses_300_second_default_wait(monkeypatch):
    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("CERTBOT_VALIDATION", "new-value")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.delenv("GODADDY_DNS_WAIT_SECONDS", raising=False)

    class Response:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(MODULE, "_fetch_existing_txt_values", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(MODULE, "_godaddy_request", lambda *_args, **_kwargs: Response())

    wait_calls: list[dict[str, object]] = []

    def fake_wait(**kwargs):
        wait_calls.append(kwargs)

    monkeypatch.setattr(MODULE, "_wait_for_dns_txt_propagation", fake_wait)
    monkeypatch.setattr(MODULE, "_wait_for_public_recursive_txt_propagation", fake_wait)

    MODULE._upsert_txt_record()

    assert wait_calls[0]["timeout_seconds"] == 300
    assert wait_calls[1]["timeout_seconds"] == 300


def test_wait_for_public_recursive_txt_propagation_raises_timeout(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 1, 1]).__next__)
    monkeypatch.setattr(
        MODULE,
        "_query_public_recursive_txt_values",
        lambda *_args, **_kwargs: ({"1.1.1.1": {"stale-value"}}, set()),
    )

    with pytest.raises(TimeoutError, match="public recursive resolver caches"):
        MODULE._wait_for_public_recursive_txt_propagation(
            challenge_domain="_acme-challenge.example.com",
            expected_value="expected-value",
            timeout_seconds=1,
        )


def test_cleanup_txt_record_removes_only_current_validation_value(monkeypatch):
    calls: list[tuple[str, str, object]] = []

    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("CERTBOT_VALIDATION", "new-value")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.setattr(
        MODULE, "_fetch_existing_txt_values", lambda *_args, **_kwargs: ["old", "new-value"]
    )

    class Response:
        status_code = 200
        text = "ok"

    def fake_request(method, path, *, payload=None):
        calls.append((method, path, payload))
        return Response()

    monkeypatch.setattr(MODULE, "_godaddy_request", fake_request)

    MODULE._cleanup_txt_record()

    assert calls == [
        (
            "PUT",
            "/v1/domains/example.com/records/TXT/_acme-challenge",
            [{"data": "old", "ttl": 600}],
        )
    ]


def test_cleanup_txt_record_requires_validation_env(monkeypatch):
    monkeypatch.setenv("CERTBOT_DOMAIN", "example.com")
    monkeypatch.setenv("GODADDY_ZONE", "example.com")
    monkeypatch.delenv("CERTBOT_VALIDATION", raising=False)

    with pytest.raises(RuntimeError, match="CERTBOT_VALIDATION"):
        MODULE._cleanup_txt_record()


def test_wait_for_public_recursive_txt_propagation_ignores_failed_resolvers(monkeypatch):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 0]).__next__)
    monkeypatch.setattr(
        MODULE,
        "_query_public_recursive_txt_values",
        lambda *_args, **_kwargs: (
            {"1.1.1.1": set(), "8.8.8.8": {"expected-value"}},
            {"1.1.1.1"},
        ),
    )

    MODULE._wait_for_public_recursive_txt_propagation(
        challenge_domain="_acme-challenge.example.com",
        expected_value="expected-value",
        timeout_seconds=1,
    )


def test_wait_for_public_recursive_txt_propagation_times_out_when_all_resolvers_fail(
    monkeypatch,
):
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(MODULE.time, "time", iter([0, 1]).__next__)
    monkeypatch.setattr(
        MODULE,
        "_query_public_recursive_txt_values",
        lambda *_args, **_kwargs: ({}, set(MODULE.PUBLIC_DNS_RESOLVERS)),
    )

    with pytest.raises(TimeoutError, match="All configured public resolvers failed"):
        MODULE._wait_for_public_recursive_txt_propagation(
            challenge_domain="_acme-challenge.example.com",
            expected_value="expected-value",
            timeout_seconds=1,
        )
