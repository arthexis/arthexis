from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0009_retire_web_request_samplers"),
    ]

    operations = [
        migrations.DeleteModel(
            name="WebSampleAttachment",
        ),
        migrations.DeleteModel(
            name="WebSample",
        ),
    ]
