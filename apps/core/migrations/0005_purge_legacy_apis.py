from django.db import migrations


LEGACY_APIS_TABLES = (
    "apis_generalservicetokenevent",
    "apis_servicetokenevent",
    "apis_generalservicetoken_security_groups",
    "apis_resourcemethod",
    "apis_generalservicetoken",
    "apis_servicetoken",
    "apis_apiexplorer",
)


def purge_legacy_apis_state(apps, schema_editor):
    """Remove persisted state owned by the retired ``apis`` Django app."""

    connection = schema_editor.connection
    alias = connection.alias

    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    legacy_content_types = ContentType.objects.using(alias).filter(app_label="apis")
    Permission.objects.using(alias).filter(
        content_type__in=legacy_content_types
    ).delete()
    legacy_content_types.delete()

    existing_tables = set(connection.introspection.table_names())
    quote_name = connection.ops.quote_name
    for table_name in LEGACY_APIS_TABLES:
        if table_name in existing_tables:
            schema_editor.execute(f"DROP TABLE {quote_name(table_name)}")


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0004_release_remaining_models"),
    ]

    operations = [
        migrations.RunPython(
            purge_legacy_apis_state,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
