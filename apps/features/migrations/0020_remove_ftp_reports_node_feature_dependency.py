from django.db import migrations


FTP_REPORTS_FEATURE_SLUG = "ocpp-ftp-reports"
FTP_SERVER_NODE_FEATURE_SLUG = "ftp-server"


def detach_ftp_reports_feature_from_node_feature(apps, schema_editor):
    """Drop the FTP suite feature dependency on the removed node feature."""

    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FTP_REPORTS_FEATURE_SLUG).update(node_feature=None)


def restore_ftp_reports_feature_node_feature(apps, schema_editor):
    """Restore the legacy FTP node feature link during rollback."""

    Feature = apps.get_model("features", "Feature")
    NodeFeature = apps.get_model("nodes", "NodeFeature")

    node_feature, _ = NodeFeature.objects.get_or_create(
        slug=FTP_SERVER_NODE_FEATURE_SLUG,
        defaults={
            "display": "FTP Server",
            "is_seed_data": True,
            "is_deleted": False,
        },
    )
    Feature.objects.filter(slug=FTP_REPORTS_FEATURE_SLUG).update(node_feature=node_feature)


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0019_merge_20260225_1819"),
        ("nodes", "0032_remove_ftp_server_node_feature"),
    ]

    operations = [
        migrations.RunPython(
            detach_ftp_reports_feature_from_node_feature,
            restore_ftp_reports_feature_node_feature,
        ),
    ]
