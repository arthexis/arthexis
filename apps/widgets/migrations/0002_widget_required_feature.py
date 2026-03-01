from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0033_merge_20260228_1251"),
        ("widgets", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="widget",
            name="required_feature",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional node feature required for this widget to render.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="widgets",
                to="nodes.nodefeature",
            ),
        ),
    ]
