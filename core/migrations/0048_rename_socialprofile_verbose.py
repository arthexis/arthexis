from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0047_socialprofile"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="socialprofile",
            options={
                "verbose_name": "Social Identity",
                "verbose_name_plural": "Social Identities",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("network", "handle"),
                        name="socialprofile_network_handle",
                    ),
                    models.UniqueConstraint(
                        fields=("network", "domain"),
                        name="socialprofile_network_domain",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("user__isnull", False), ("group__isnull", True)),
                            models.Q(("user__isnull", True), ("group__isnull", False)),
                            _connector="OR",
                        ),
                        name="socialprofile_requires_owner",
                    ),
                ],
            },
        ),
    ]
