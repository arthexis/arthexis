from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("ftp", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="ftpfolder",
            old_name="owner",
            new_name="user",
        ),
        migrations.RenameField(
            model_name="ftpfolder",
            old_name="security_group",
            new_name="group",
        ),
        migrations.AlterField(
            model_name="ftpfolder",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text="User that owns this object.",
                null=True,
                on_delete=models.CASCADE,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="ftpfolder",
            name="group",
            field=models.ForeignKey(
                blank=True,
                help_text="Security group that owns this object.",
                null=True,
                on_delete=models.CASCADE,
                related_name="+",
                to="groups.securitygroup",
            ),
        ),
        migrations.AddConstraint(
            model_name="ftpfolder",
            constraint=models.CheckConstraint(
                condition=(
                    (Q(user__isnull=True) & Q(group__isnull=True))
                    | (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="ftp_ftpfolder_owner_exclusive",
            ),
        ),
    ]
