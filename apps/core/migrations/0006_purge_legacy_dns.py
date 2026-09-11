from django.db import migrations


LEGACY_DNS_TABLES = (
    "nodes_dnsrecord",
    "dns_dnsproxyconfig",
    "dns_dnsprovidercredential",
)


def purge_legacy_dns_state(apps, schema_editor):
    """Remove persisted state owned by the retired ``dns`` Django app."""

    connection = schema_editor.connection
    alias = connection.alias

    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    legacy_content_types = ContentType.objects.using(alias).filter(app_label="dns")
    Permission.objects.using(alias).filter(
        content_type__in=legacy_content_types
    ).delete()
    legacy_content_types.delete()

    existing_tables = set(connection.introspection.table_names())
    quote_name = connection.ops.quote_name
    for table_name in LEGACY_DNS_TABLES:
        if table_name in existing_tables:
            schema_editor.execute(f"DROP TABLE {quote_name(table_name)}")


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("certs", "0003_remove_certbot_certificate"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0005_purge_legacy_apis"),
    ]

    operations = [
        migrations.RunPython(
            purge_legacy_dns_state,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
