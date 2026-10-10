from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from tools.ocpp_collector_token import encode_jwt


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible" / "roles" / "ocpp_collector"
DEFAULTS = ROLE / "defaults" / "main.yml"
TASKS = ROLE / "tasks" / "main.yml"
BOOTSTRAP = ROLE / "templates" / "bootstrap.sql.j2"
SCHEMA = ROLE / "templates" / "schema.sql.j2"
ENROLLMENT = ROLE / "templates" / "enrollment.sql.j2"
POSTGREST = ROLE / "templates" / "postgrest.conf.j2"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _decode(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def test_forwarder_token_contains_role_and_satellite_identity():
    secret = "s" * 32
    token = encode_jwt(
        secret=secret,
        role="ocpp_forwarder",
        ttl=3600,
        satellite_id="gway-004",
    )

    header, payload, signature = token.split(".")
    claims = _decode(payload)

    assert _decode(header) == {"alg": "HS256", "typ": "JWT"}
    assert claims["role"] == "ocpp_forwarder"
    assert claims["satellite_id"] == "gway-004"
    assert claims["exp"] > claims["iat"]

    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert signature == expected


def test_reader_token_has_no_satellite_identity():
    token = encode_jwt(secret="r" * 32, role="arthexis_reader", ttl=3600)
    claims = _decode(token.split(".")[1])

    assert claims["role"] == "arthexis_reader"
    assert "satellite_id" not in claims


def test_token_utility_rejects_weak_secret():
    with pytest.raises(ValueError, match="at least 32"):
        encode_jwt(secret="short", role="ocpp_forwarder", ttl=3600, satellite_id="gway-004")


def test_postgrest_requires_configured_jwt_secret():
    config = read(POSTGREST)
    assert 'jwt-secret = "{{ ocpp_collector_jwt_secret }}"' in config


def test_bootstrap_creates_non_login_api_roles_and_grants_them_to_authenticator():
    sql = read(BOOTSTRAP)

    assert "CREATE ROLE ocpp_forwarder NOLOGIN" in sql
    assert "CREATE ROLE arthexis_reader NOLOGIN" in sql
    assert "GRANT ocpp_forwarder TO {{ ocpp_collector_db_role }}" in sql
    assert "GRANT arthexis_reader TO {{ ocpp_collector_db_role }}" in sql


def test_forwarder_permissions_are_write_without_delete_and_reader_is_select_only():
    sql = read(SCHEMA)

    assert "GRANT SELECT, INSERT, UPDATE ON api.events TO ocpp_forwarder" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON api.transactions TO ocpp_forwarder" in sql
    assert "GRANT SELECT ON\n    api.satellites," in sql
    assert "TO arthexis_reader;" in sql
    assert "GRANT DELETE" not in sql


def test_all_replicated_tables_enable_row_level_security():
    sql = read(SCHEMA)
    for table in ("satellites", "chargers", "events", "transactions", "energy_samples"):
        assert f"ALTER TABLE api.{table} ENABLE ROW LEVEL SECURITY" in sql


def test_forwarder_rls_binds_rows_to_jwt_satellite_and_enabled_enrollment():
    sql = read(SCHEMA)

    assert "current_setting('request.jwt.claims', true)" in sql
    assert "satellite_id = collector_private.jwt_satellite_id()" in sql
    assert "collector_private.satellite_is_enabled(satellite_id)" in sql
    assert "TO ocpp_forwarder" in sql


def test_arthexis_reader_rls_is_fleet_wide_but_select_only():
    sql = read(SCHEMA)

    assert "CREATE POLICY arthexis_reader_{{ table }}" in sql
    assert "{% for table in" in sql
    assert "events" in sql
    assert "FOR SELECT" in sql
    assert "TO arthexis_reader" in sql
    assert "USING (true)" in sql


def test_enrollment_is_authoritative_and_disables_omitted_satellites():
    sql = read(ENROLLMENT)

    assert "UPDATE api.satellites\nSET enabled = false;" in sql
    assert "ON CONFLICT (satellite_id) DO UPDATE" in sql
    assert "SET enabled = EXCLUDED.enabled" in sql


def test_auth_defaults_require_external_secret_and_explicit_enrollment():
    defaults = read(DEFAULTS)

    assert 'ocpp_collector_jwt_secret: ""' in defaults
    assert "ocpp_collector_satellites: []" in defaults


def test_ansible_installs_operator_token_utility_with_ocpp_prefix():
    tasks = read(TASKS)

    assert "Install OCPP Collector token utility" in tasks
    assert "dest: /usr/local/bin/ocpp-collector-token" in tasks
