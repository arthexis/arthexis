from django.db import migrations


FTP_SERVER_SLUG = "ftp-server"
FTP_SERVER_DISPLAY = "FTP Server"
FTP_SERVER_ROLE_NAMES = ("Terminal", "Control", "Watchtower")


def remove_ftp_server_node_feature(apps, schema_editor):
    """Remove the legacy FTP node feature now covered by a suite feature."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeFeature.objects.filter(slug=FTP_SERVER_SLUG).delete()


def restore_ftp_server_node_feature(apps, schema_editor):
    """Restore the legacy FTP node feature for migration rollback."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")

    feature, _ = NodeFeature.objects.get_or_create(
        slug=FTP_SERVER_SLUG,
        defaults={
            "display": FTP_SERVER_DISPLAY,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    if feature.display != FTP_SERVER_DISPLAY:
        feature.display = FTP_SERVER_DISPLAY
    if not feature.is_seed_data:
        feature.is_seed_data = True
    if feature.is_deleted:
        feature.is_deleted = False
    feature.save(update_fields=["display", "is_seed_data", "is_deleted"])

    roles = list(NodeRole.objects.filter(name__in=FTP_SERVER_ROLE_NAMES))
    if roles:
        feature.roles.add(*roles)


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0031_merge_20260226_1839"),
    ]

    operations = [
        migrations.RunPython(
            remove_ftp_server_node_feature,
            restore_ftp_server_node_feature,
        ),
    ]
