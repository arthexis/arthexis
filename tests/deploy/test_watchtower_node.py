from pathlib import Path

import node.watchtower as watchtower
from node.watchtower.security import audit


def test_watchtower_status_aggregates_application_and_remote_services(monkeypatch):
    monkeypatch.setattr(watchtower, "application_ready", lambda **kwargs: True)
    monkeypatch.setattr(watchtower, "_listener", lambda host, port: port != 8001)

    result = watchtower.status()

    assert result["role"] == "watchtower"
    assert result["ok"] is False
    assert [item["name"] for item in result["checks"]] == [
        "application",
        "mcp",
        "oauth",
    ]
    assert [item["ok"] for item in result["checks"]] == [True, True, False]


def test_watchtower_diagnose_includes_status_django_and_role(
    tmp_path,
    monkeypatch,
):
    role = tmp_path / "role"
    role.write_text("watchtower\n", encoding="utf-8")
    monkeypatch.setattr(watchtower, "ROLE_FILE", role)
    monkeypatch.setattr(
        watchtower,
        "status",
        lambda **kwargs: {
            "role": "watchtower",
            "ok": True,
            "checks": [{"name": "application", "ok": True}],
        },
    )

    result = watchtower.diagnose()

    assert result["role"] == "watchtower"
    assert {item["name"] for item in result["checks"]} == {
        "application",
        "django",
        "role",
    }
    assert next(item for item in result["checks"] if item["name"] == "role") == {
        "name": "role",
        "ok": True,
        "detail": "watchtower",
    }


def test_watchtower_security_audit_checks_loopback_and_state_permissions(
    tmp_path,
    monkeypatch,
):
    state = tmp_path / "state.sqlite"
    state.write_text("state", encoding="utf-8")
    state.chmod(0o600)
    import node.watchtower.security as security

    monkeypatch.setattr(security, "_listener", lambda host, port: True)

    result = audit(security_state=str(state))

    assert result["role"] == "watchtower"
    assert result["ok"] is True
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["mcp_loopback"]["ok"] is True
    assert checks["oauth_loopback"]["ok"] is True
    assert checks["security_state_exists"]["ok"] is True
    assert checks["security_state_not_group_world_writable"]["ok"] is True


def test_watchtower_security_audit_rejects_non_loopback_boundary(
    tmp_path,
    monkeypatch,
):
    state = tmp_path / "state.sqlite"
    state.write_text("state", encoding="utf-8")
    state.chmod(0o600)
    import node.watchtower.security as security

    monkeypatch.setattr(security, "_listener", lambda host, port: True)

    result = audit(host="0.0.0.0", security_state=str(state))

    assert result["ok"] is False
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["mcp_loopback"]["ok"] is False
    assert checks["oauth_loopback"]["ok"] is False


def test_watchtower_gway_surfaces_establish_role_context():
    watchtower_recipe = Path("deploy/watchtower.rx").read_text(encoding="utf-8")
    mcp_recipe = Path("deploy/mcp-server.rx").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/watchtower-deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "default --role watchtower" in watchtower_recipe
    assert "default --role watchtower" in mcp_recipe
    assert (
        'gway default --role watchtower ";" "$@"'
        in workflow
    )


def test_watchtower_node_family_is_packaged_and_guided():
    project = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"node*"' in project
    assert "[[tool.gway.roles.watchtower.guide]]" in project
    assert 'command = "node status"' in project
    assert 'command = "node diagnose"' in project
    assert 'command = "node security audit"' in project
