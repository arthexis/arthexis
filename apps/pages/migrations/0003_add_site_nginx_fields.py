from django.db import migrations, models
import django.db.models.deletion


def apply_site_fields(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    SiteTemplate = apps.get_model("pages", "SiteTemplate")
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        existing_columns = {
            column.name for column in connection.introspection.get_table_description(cursor, Site._meta.db_table)
        }

    def _add_boolean_field(column_name: str, default_sql: str) -> None:
        if column_name in existing_columns:
            return
        schema_editor.execute(
            f"ALTER TABLE {schema_editor.quote_name(Site._meta.db_table)} "
            f"ADD COLUMN {schema_editor.quote_name(column_name)} BOOLEAN NOT NULL DEFAULT {default_sql}"
        )

    default_sql = "0" if connection.vendor == "sqlite" else "FALSE"
    _add_boolean_field("managed", default_sql)
    _add_boolean_field("require_https", default_sql)

    if "template_id" not in existing_columns:
        field = models.ForeignKey(
            SiteTemplate,
            on_delete=django.db.models.deletion.SET_NULL,
            related_name="sites",
            null=True,
            blank=True,
            verbose_name="Template",
        )
        field.set_attributes_from_name("template")
        schema_editor.add_field(Site, field)


def remove_site_fields(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    for name in ("template", "require_https", "managed"):
        try:
            field = Site._meta.get_field(name)
        except Exception:
            continue
        schema_editor.remove_field(Site, field)


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0002_move_chat_models"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.RunPython(apply_site_fields, remove_site_fields),
    ]
