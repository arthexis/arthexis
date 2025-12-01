import os

import pytest
import django
from django.test import TestCase, TransactionTestCase
from django.test.runner import DiscoverRunner

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django.setup()

# Skip serialized rollbacks to avoid cloning a potentially stale primary database
# when setting up Django's test database.
TransactionTestCase.serialized_rollback = False
TestCase.serialized_rollback = False


# The project dynamically augments ``django.contrib.sites.Site`` with additional
# fields, but the SQLite schema shipped with the repository does not include
# those columns. When the Django test runner tries to serialize the database
# before creating the test database, the mismatch triggers OperationalError
# exceptions. Drop the extra fields for tests so the model definition matches
# the available schema.
from contextlib import suppress
from django.contrib.sites.models import Site
from django.db.models.signals import post_migrate
from apps.pages import site_config

for _field_name in ("managed", "require_https", "template"):
    _field = Site._meta._forward_fields_map.pop(_field_name, None)
    if _field and _field in Site._meta.local_fields:
        Site._meta.local_fields.remove(_field)
    with suppress(AttributeError):
        if _field and _field in Site._meta.fields:
            Site._meta.fields.remove(_field)
    if hasattr(Site, _field_name):
        delattr(Site, _field_name)

post_migrate.disconnect(
    receiver=site_config._run_post_migrate_update,
    dispatch_uid="pages_site_post_migrate_update",
)


class NoSerializeRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        return super().setup_databases(serialized_aliases=set(), **kwargs)


@pytest.fixture(scope="session", autouse=True)
def django_test_environment():
    runner = NoSerializeRunner(serialize=False)
    runner.setup_test_environment()
    old_config = runner.setup_databases()
    try:
        yield
    finally:
        runner.teardown_databases(old_config)
        runner.teardown_test_environment()
