from __future__ import annotations

import textwrap

import pytest

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
    discovered, errors = discover_odoo_configs([sample_config])

    assert errors == []
    assert len(discovered) == 1

    options = discovered[0].options
    assert options["db_host"] == "localhost"
    assert options["db_name"] == "odoo"
    assert options["admin_passwd"] == "supersecret"


@pytest.mark.django_db
def test_sync_odoo_deployments_creates_and_updates(sample_config):
    initial = sync_odoo_deployments([sample_config])

    assert initial["created"] == 1
    assert initial["updated"] == 0
    deployment = OdooDeployment.objects.get(config_path=str(sample_config))
    assert deployment.db_name == "odoo"
    assert deployment.db_port == 5432
    assert deployment.http_port == 8069

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

    updated = sync_odoo_deployments([sample_config])

    assert updated["created"] == 0
    assert updated["updated"] == 1

    deployment.refresh_from_db()
    assert deployment.db_name == "updated"
    assert deployment.db_port == 5433
    assert deployment.http_port == 8070
