from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_user_allow_local_network_passwordless_login"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="allow_local_network_passwordless_login",
            field=models.BooleanField(
                default=False,
                help_text=_(
                    "Allow this non-staff user to sign in from local IPv4 /16 peers; users with a usable password must still provide it."
                ),
                verbose_name="allow local network passwordless login",
            ),
        ),
    ]
