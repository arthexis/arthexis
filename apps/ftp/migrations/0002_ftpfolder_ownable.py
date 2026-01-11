from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def remove_conflicting_ftpfolder_owners(apps, schema_editor):
    ftp_folder = apps.get_model("ftp", "FTPFolder")
    ftp_folder.objects.filter(user__isnull=False, group__isnull=False).update(
        group=None,
    )


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
                on_delete=models.SET_NULL,
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
                on_delete=models.SET_NULL,
                related_name="+",
                to="groups.securitygroup",
            ),
        ),
        migrations.RunPython(
            code=remove_conflicting_ftpfolder_owners,
            reverse_code=migrations.RunPython.noop,
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
