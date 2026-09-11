"""Regression coverage for retiring the legacy APIs token app."""

from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection

from utils.role_app_profiles import (
    FEATURE_PACK_APP_SELECTORS,
    RETIRED_RUNTIME_APP_SELECTORS,
    explain_role_app_selectors,
    resolve_role_app_selectors,
)

purge_migration = import_module("apps.core.migrations.0005_purge_legacy_apis")


def test_apis_app_cannot_be_selected_at_runtime():
    assert "apps.apis" in RETIRED_RUNTIME_APP_SELECTORS
    assert "api_service_tokens" not in FEATURE_PACK_APP_SELECTORS
    assert "apps.apis" not in resolve_role_app_selectors("control")

    result = explain_role_app_selectors(
        "control",
        explicit_apps=("apps.apis",),
        required_apps={},
    )

    assert "apps.apis" not in result.selectors


def test_retired_api_service_tokens_feature_pack_is_rejected():
    with pytest.raises(ValueError, match="Unknown feature pack"):
        resolve_role_app_selectors(
            "satellite",
            feature_packs=("api_service_tokens",),
        )


@pytest.mark.django_db(transaction=True)
def test_purge_legacy_apis_state_removes_rows_metadata_and_is_idempotent():
    quote_name = connection.ops.quote_name
    create_statements = (
        "CREATE TABLE apis_servicetoken (id INTEGER PRIMARY KEY)",
        (
            "CREATE TABLE apis_servicetokenevent "
            "(id INTEGER PRIMARY KEY, token_id INTEGER REFERENCES apis_servicetoken(id))"
        ),
        "CREATE TABLE apis_generalservicetoken (id INTEGER PRIMARY KEY)",
        (
            "CREATE TABLE apis_generalservicetokenevent "
            "(id INTEGER PRIMARY KEY, token_id INTEGER REFERENCES apis_generalservicetoken(id))"
        ),
        (
            "CREATE TABLE apis_generalservicetoken_security_groups "
            "(id INTEGER PRIMARY KEY, generalservicetoken_id INTEGER "
            "REFERENCES apis_generalservicetoken(id), securitygroup_id INTEGER)"
        ),
        "CREATE TABLE apis_apiexplorer (id INTEGER PRIMARY KEY)",
        (
            "CREATE TABLE apis_resourcemethod "
            "(id INTEGER PRIMARY KEY, api_id INTEGER REFERENCES apis_apiexplorer(id))"
        ),
    )
    with connection.cursor() as cursor:
        for statement in create_statements:
            cursor.execute(statement)
        for table_name in purge_migration.LEGACY_APIS_TABLES:
            cursor.execute(f"INSERT INTO {quote_name(table_name)} (id) VALUES (1)")

    content_type = ContentType.objects.create(app_label="apis", model="servicetoken")
    Permission.objects.create(
        content_type=content_type,
        codename="manage_service_tokens",
        name="Can manage service tokens",
    )

    with connection.schema_editor() as schema_editor:
        purge_migration.purge_legacy_apis_state(django_apps, schema_editor)

    remaining_tables = set(connection.introspection.table_names())
    assert set(purge_migration.LEGACY_APIS_TABLES).isdisjoint(remaining_tables)
    assert not ContentType.objects.filter(app_label="apis").exists()
    assert not Permission.objects.filter(content_type__app_label="apis").exists()

    # A clean database must make the cleanup a harmless no-op.
    with connection.schema_editor() as schema_editor:
        purge_migration.purge_legacy_apis_state(django_apps, schema_editor)
