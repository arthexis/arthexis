from django.db import migrations, models

from apps.media.migrations_utils import copy_to_media

SSH_KEY_BUCKET_SLUG = "credentials-ssh-keys"
SSH_KEY_ALLOWED_PATTERNS = "\n".join(["id_*", "*.pem", "*.pub", "*.key", "*.ppk"])


def migrate_ssh_keys(apps, schema_editor):
    SSHAccount = apps.get_model("credentials", "SSHAccount")
    MediaBucket = apps.get_model("media", "MediaBucket")
    MediaFile = apps.get_model("media", "MediaFile")

    bucket, _ = MediaBucket.objects.update_or_create(
        slug=SSH_KEY_BUCKET_SLUG,
        defaults={
            "name": "SSH Keys",
            "allowed_patterns": SSH_KEY_ALLOWED_PATTERNS,
            "max_bytes": 128 * 1024,
            "expires_at": None,
        },
    )

    for account in SSHAccount.objects.exclude(private_key=""):
        old_file = getattr(account, "private_key", None)
        media_file = copy_to_media(bucket, MediaFile, old_file)
        if media_file:
            SSHAccount.objects.filter(pk=account.pk).update(private_key_media=media_file)

    for account in SSHAccount.objects.exclude(public_key=""):
        old_file = getattr(account, "public_key", None)
        media_file = copy_to_media(bucket, MediaFile, old_file)
        if media_file:
            SSHAccount.objects.filter(pk=account.pk).update(public_key_media=media_file)


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
        ("credentials", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sshaccount",
            name="private_key_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="ssh_private_keys",
                to="media.mediafile",
                verbose_name="Private key",
            ),
        ),
        migrations.AddField(
            model_name="sshaccount",
            name="public_key_media",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="ssh_public_keys",
                to="media.mediafile",
                verbose_name="Public key",
            ),
        ),
        migrations.RunPython(migrate_ssh_keys, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="sshaccount",
            name="private_key",
        ),
        migrations.RemoveField(
            model_name="sshaccount",
            name="public_key",
        ),
    ]
