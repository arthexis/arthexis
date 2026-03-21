from django.db import migrations


class Migration(migrations.Migration):
    """Remove the retired Mermaid Flow model table."""

    dependencies = [("mermaid", "0001_initial")]

    operations = [
        migrations.DeleteModel(name="Flow"),
    ]
