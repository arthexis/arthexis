from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.odoo import services
from apps.odoo.models import OdooDeployment
from apps.odoo.services import discover_odoo_configs, sync_odoo_deployments


@pytest.fixture
def sample_config(tmp_path):
    config_path = tmp_path / "odoo.conf"
    config_path.write_text(
        textwrap.dedent(
            """
            [options]
            admin_passwd = supersecret
            db_host = localhost
            db_port = 5432
            db_user = odoo
            db_password = dbpass
            db_name = odoo
            dbfilter = ^odoo$
            addons_path = /opt/odoo/addons,/opt/odoo/custom
            data_dir = /var/lib/odoo
            logfile = /var/log/odoo/odoo.log
            http_port = 8069
            longpolling_port = 8072
            """
        ).strip()
    )
    return config_path


@pytest.mark.django_db
def test_discover_odoo_configs_reads_options(sample_config):
    discovered, errors = discover_odoo_configs([sample_config], scan_filesystem=False)

    assert errors == []
    assert len(discovered) == 1

    options = discovered[0].options
    assert options["db_host"] == "localhost"
    assert options["db_name"] == "odoo"
    assert options["admin_passwd"] == "supersecret"
    assert discovered[0].base_path == sample_config.parent


@pytest.mark.django_db
def test_sync_odoo_deployments_creates_and_updates(sample_config):
    initial = sync_odoo_deployments([sample_config], scan_filesystem=False)

    assert initial["created"] == 1
    assert initial["updated"] == 0
    deployment = OdooDeployment.objects.get(config_path=str(sample_config))
    assert deployment.db_name == "odoo"
    assert deployment.db_port == 5432
    assert deployment.http_port == 8069
    assert deployment.base_path == str(sample_config.parent)

    sample_config.write_text(
        textwrap.dedent(
            """
            [options]
            admin_passwd = supersecret
            db_host = localhost
            db_port = 5433
            db_user = odoo
            db_password = dbpass
            db_name = updated
            addons_path = /opt/odoo/addons
            data_dir = /var/lib/odoo
            http_port = 8070
            """
        ).strip()
    )

    updated = sync_odoo_deployments([sample_config], scan_filesystem=False)

    assert updated["created"] == 0
    assert updated["updated"] == 1

    deployment.refresh_from_db()
    assert deployment.db_name == "updated"
    assert deployment.db_port == 5433
    assert deployment.http_port == 8070


def test_discover_odoo_configs_uses_user_home(monkeypatch, tmp_path):
    user_home = tmp_path / "home" / "demo"
    user_config = user_home / ".config" / "odoo" / "odoo.conf"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("[options]\ninstance_name=demo")

    monkeypatch.setattr(
        "apps.odoo.services._default_config_locations", lambda: [user_config]
    )

    discovered, errors = discover_odoo_configs(scan_filesystem=False)

    assert errors == []
    assert [entry.path for entry in discovered] == [user_config]
    assert discovered[0].base_path == user_config.parent


def test_discover_odoo_configs_searches_home_tree(monkeypatch, tmp_path):
    home_root = tmp_path / "home"
    nested_config = home_root / "ubuntu" / "www.gelectriic.com" / "odoo.conf"
    nested_config.parent.mkdir(parents=True)
    nested_config.write_text("[options]\ninstance_name=deep")

    monkeypatch.setattr(
        "apps.odoo.services._default_config_locations", lambda: [home_root]
    )

    discovered, errors = discover_odoo_configs(scan_filesystem=False)

    assert errors == []
    assert [entry.path for entry in discovered] == [nested_config]
    assert discovered[0].base_path == nested_config.parent


def test_default_config_locations_excludes_home_root(monkeypatch):
    fake_home = Path("/home/demo")

    monkeypatch.setattr("apps.odoo.services.Path.home", lambda: fake_home)
    monkeypatch.setattr("apps.odoo.services._get_uid", lambda: 1000)

    if services.pwd is None:
        monkeypatch.setattr(
            "apps.odoo.services.pwd",
            SimpleNamespace(getpwuid=lambda _: SimpleNamespace(pw_name="demo")),
        )
    else:
        monkeypatch.setattr(
            "apps.odoo.services.pwd.getpwuid",
            lambda _: SimpleNamespace(pw_name="demo"),
        )

    locations = services._default_config_locations()

    assert Path("/home") not in locations
    assert locations == [
        fake_home / ".odoorc",
        fake_home / ".config/odoo/odoo.conf",
        fake_home,
    ]
