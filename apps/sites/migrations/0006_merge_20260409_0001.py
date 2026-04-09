from django.db import migrations


class Migration(migrations.Migration):

    # NOTE: The runtime app package is ``apps.sites`` but its Django app label
    # remains ``pages`` for migration/model history compatibility.
    dependencies = [
        ("pages", "0002_initial"),
        ("pages", "0003_initial"),
        ("pages", "0004_initial"),
        ("pages", "0005_siteprofile"),
    ]

    operations = []
