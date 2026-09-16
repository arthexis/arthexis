from django.db import migrations


def purge_certbot_certificates(apps, schema_editor):
    CertbotCertificate = apps.get_model("certs", "CertbotCertificate")
    CertificateBase = apps.get_model("certs", "CertificateBase")
    certificate_ids = list(
        CertbotCertificate.objects.values_list("pk", flat=True)
    )
    CertbotCertificate.objects.all().delete()
    if certificate_ids:
        CertificateBase.objects.filter(pk__in=certificate_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("certs", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(purge_certbot_certificates, migrations.RunPython.noop),
        migrations.DeleteModel(name="CertbotCertificate"),
    ]
