from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.odoo.models import OdooDeployment, OdooEmployee


@pytest.mark.django_db
def test_odoo_command_status_mode_outputs_integration_summary(admin_user):
    """The command without arguments prints an integration health summary."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )
    OdooDeployment.objects.create(
        name="Local Odoo",
        config_path="/etc/odoo/odoo.conf",
        base_path="/etc/odoo",
        last_discovered=timezone.now(),
    )

    out = StringIO()
    call_command("odoo", stdout=out)
    output = out.getvalue()

    assert "Odoo Integration Status" in output
    assert "Profiles: total=1, verified=1" in output
    assert "Deployments: total=1" in output


@pytest.mark.django_db
def test_odoo_command_rpc_mode_passes_params_and_kwargs(admin_user, monkeypatch):
    """RPC mode forwards --params and --kwargs to the Odoo profile execute method."""

    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    captured: dict[str, object] = {}

    def fake_execute(self, model, method, *args, **kwargs):
        captured["model"] = model
        captured["method"] = method
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return [{"id": 1, "name": "SO001"}]

    monkeypatch.setattr(OdooEmployee, "execute", fake_execute)

    out = StringIO()
    call_command(
        "odoo",
        "--profile-id",
        str(profile.pk),
        "--model",
        "sale.order",
        "--method",
        "search_read",
        "--params",
        '[[["state", "=", "sale"]]]',
        "--kwargs",
        '{"fields": ["name"], "limit": 1}',
        stdout=out,
    )

    payload = json.loads(out.getvalue())
    assert captured == {
        "model": "sale.order",
        "method": "search_read",
        "args": [[["state", "=", "sale"]]],
        "kwargs": {"fields": ["name"], "limit": 1},
    }
    assert payload["result"] == [{"id": 1, "name": "SO001"}]
    assert payload["profile_id"] == profile.pk


@pytest.mark.django_db
def test_odoo_command_rpc_mode_requires_model_and_method(admin_user):
    """RPC mode rejects partial model/method input to prevent ambiguous calls."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    with pytest.raises(CommandError, match="Both --model and --method are required"):
        call_command("odoo", "--model", "sale.order")


@pytest.mark.django_db
def test_odoo_command_rpc_mode_rejects_invalid_params_json(admin_user):
    """RPC mode returns a clear error when --params is not valid JSON."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    with pytest.raises(CommandError, match=r"Invalid JSON for --params"):
        call_command(
            "odoo",
            "--model",
            "sale.order",
            "--method",
            "search_read",
            "--params",
            "not-json",
        )


@pytest.mark.django_db
def test_odoo_command_rpc_mode_rejects_invalid_kwargs_json(admin_user):
    """RPC mode returns a clear error when --kwargs is not valid JSON."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    with pytest.raises(CommandError, match=r"Invalid JSON for --kwargs"):
        call_command(
            "odoo",
            "--model",
            "sale.order",
            "--method",
            "search_read",
            "--kwargs",
            "not-json",
        )


@pytest.mark.django_db
def test_odoo_command_rpc_mode_requires_verified_profile_when_none_available(admin_user):
    """RPC mode fails when there is no verified profile to select by default."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
    )

    with pytest.raises(CommandError, match=r"No verified Odoo profile is available"):
        call_command("odoo", "--model", "sale.order", "--method", "search_read")


@pytest.mark.django_db
def test_odoo_command_rpc_mode_rejects_unverified_profile_id(admin_user):
    """RPC mode fails when --profile-id points to an unverified profile."""

    profile = OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
    )

    with pytest.raises(CommandError, match=rf"Odoo profile id={profile.pk} is not verified"):
        call_command(
            "odoo",
            "--profile-id",
            str(profile.pk),
            "--model",
            "sale.order",
            "--method",
            "search_read",
        )


@pytest.mark.django_db
def test_odoo_command_rpc_mode_rejects_unknown_profile_id(admin_user):
    """RPC mode fails when --profile-id does not exist."""

    OdooEmployee.objects.create(
        user=admin_user,
        host="https://odoo.example.com",
        database="odoodb",
        username="admin",
        password="secret",
        odoo_uid=99,
        verified_on=timezone.now(),
    )

    with pytest.raises(CommandError, match=r"Odoo profile id=999999 does not exist"):
        call_command(
            "odoo",
            "--profile-id",
            "999999",
            "--model",
            "sale.order",
            "--method",
            "search_read",
        )
