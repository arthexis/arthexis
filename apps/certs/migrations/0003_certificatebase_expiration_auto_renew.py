from django.db import migrations, models


def set_auto_renew_true(apps, schema_editor):
    CertificateBase = apps.get_model("certs", "CertificateBase")
    CertificateBase.objects.update(auto_renew=True)


class Migration(migrations.Migration):
    dependencies = [
        ("certs", "0002_alter_certificatebase_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="certificatebase",
            name="expiration_date",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="certificatebase",
            name="auto_renew",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(set_auto_renew_true, migrations.RunPython.noop),
    ]
