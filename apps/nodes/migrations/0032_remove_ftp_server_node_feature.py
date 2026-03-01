from django.db import migrations


FTP_SERVER_SLUG = "ftp-server"
FTP_SERVER_DISPLAY = "FTP Server"
FTP_SERVER_ROLE_NAMES = ("Terminal", "Control", "Watchtower")


def remove_ftp_server_node_feature(apps, schema_editor):
    """Remove the legacy FTP node feature now covered by a suite feature."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    db_alias = schema_editor.connection.alias
    NodeFeature.objects.using(db_alias).filter(slug=FTP_SERVER_SLUG).delete()


def restore_ftp_server_node_feature(apps, schema_editor):
    """Restore the legacy FTP node feature for migration rollback."""

    NodeFeature = apps.get_model("nodes", "NodeFeature")
    NodeRole = apps.get_model("nodes", "NodeRole")
    db_alias = schema_editor.connection.alias
    feature_manager = getattr(NodeFeature, "all_objects", NodeFeature._base_manager).using(
        db_alias
    )

    feature, created = feature_manager.get_or_create(
        slug=FTP_SERVER_SLUG,
        defaults={
            "display": FTP_SERVER_DISPLAY,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    if not created:
        update_fields = []
        if feature.display != FTP_SERVER_DISPLAY:
            feature.display = FTP_SERVER_DISPLAY
            update_fields.append("display")
        if not feature.is_seed_data:
            feature.is_seed_data = True
            update_fields.append("is_seed_data")
        if feature.is_deleted:
            feature.is_deleted = False
            update_fields.append("is_deleted")
        if update_fields:
            feature.save(update_fields=update_fields)

    roles = list(NodeRole.objects.using(db_alias).filter(name__in=FTP_SERVER_ROLE_NAMES))
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
