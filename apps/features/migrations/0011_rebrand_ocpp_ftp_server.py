from __future__ import annotations

from django.db import migrations


OLD_VALUES = {
    "display": "OCPP FTP Report Uploads",
    "summary": "Provide FTP endpoints for charge points to upload diagnostics and report archives.",
    "admin_requirements": "Offer a quick action to configure and link a local FTP server for charge points configured to upload diagnostics.",
    "service_requirements": "Expose an embedded FTP server for OCPP report uploads and keep charge points linked to the local FTP configuration.",
}

NEW_VALUES = {
    "display": "OCPP-aware FTP Server",
    "summary": "Provide an OCPP-aware FTP server for charge points to upload diagnostics and report archives.",
    "admin_requirements": "Offer a quick action to configure and link the OCPP-aware local FTP server for charge points uploading diagnostics.",
    "service_requirements": "Expose an embedded OCPP-aware FTP server for report uploads and keep charge points linked to local FTP configuration.",
}


def _update_ocpp_ftp_feature(apps, values: dict[str, str]) -> None:
    """Update the seeded OCPP FTP feature fields using the provided values."""
    Feature = apps.get_model("features", "Feature")
    manager = getattr(Feature, "all_objects", Feature._base_manager)
    manager.filter(slug="ocpp-ftp-reports").update(**values)


def apply_rebrand(apps, schema_editor) -> None:
    """Apply the OCPP-aware FTP Server rebrand for the seeded suite feature."""
    del schema_editor
    _update_ocpp_ftp_feature(apps, NEW_VALUES)


def reverse_rebrand(apps, schema_editor) -> None:
    """Revert the OCPP-aware FTP Server rebrand for the seeded suite feature."""
    del schema_editor
    _update_ocpp_ftp_feature(apps, OLD_VALUES)


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0010_seed_ocpp_version_charge_point_features"),
    ]

    operations = [
        migrations.RunPython(apply_rebrand, reverse_rebrand),
    ]
