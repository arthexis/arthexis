from django.db import migrations, models


class AddFieldToSites(migrations.AddField):
    """Add a field to the ``django.contrib.sites`` ``Site`` model.

    ``AddField`` uses the migration's ``app_label`` (``pages`` here) for both
    state and database operations. We override the relevant methods so the
    operation targets the ``sites`` app instead, keeping the migration graph
    consistent for subsequent migrations that rely on the new field.
    """

    def state_forwards(self, app_label, state):
        super().state_forwards("sites", state)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        super().database_forwards("sites", schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        super().database_backwards("sites", schema_editor, from_state, to_state)


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0047_sitetemplate"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        AddFieldToSites(
            model_name="site",
            name="template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="sites",
                to="pages.sitetemplate",
                verbose_name="Template",
            ),
        ),
    ]
