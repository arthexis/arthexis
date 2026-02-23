from __future__ import annotations

import warnings

from django.db import migrations
from django.utils import timezone


NEW_VALUES = {
    "display": "OCPP-aware FTP Server",
    "summary": "Provide an OCPP-aware FTP server for charge points to upload diagnostics and report archives.",
    "admin_requirements": "Offer a quick action to configure and link the OCPP-aware local FTP server for charge points uploading diagnostics.",
    "service_requirements": "Expose an embedded OCPP-aware FTP server for report uploads and keep charge points linked to local FTP configuration.",
}

# NOTE:
# Migration 0007 seeds this feature from the fixture at runtime, and the fixture now
# contains the OCPP-aware wording. Reversing 0011 therefore needs to preserve that
# post-0010 baseline to keep rollback behavior path-independent.
BASELINE_VALUES = NEW_VALUES


def _update_ocpp_ftp_feature(apps, values: dict[str, str]) -> None:
    """Update the seeded OCPP FTP feature fields using the provided values."""
    Feature = apps.get_model("features", "Feature")
    manager = getattr(Feature, "all_objects", Feature._base_manager)
    updated = manager.filter(slug="ocpp-ftp-reports").update(
        updated_at=timezone.localtime(), **values
    )
    if updated == 0:
        warnings.warn(
            "0011_rebrand_ocpp_ftp_server: no Feature row with slug='ocpp-ftp-reports' "
            "was found; migration applied with no effect.",
            stacklevel=2,
        )


def apply_rebrand(apps, _) -> None:
    """Apply the OCPP-aware FTP Server rebrand for the seeded suite feature."""
    _update_ocpp_ftp_feature(apps, NEW_VALUES)


def reverse_rebrand(apps, _) -> None:
    """Revert to the post-0010 seeded wording for the suite feature."""
    _update_ocpp_ftp_feature(apps, BASELINE_VALUES)


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0010_seed_ocpp_version_charge_point_features"),
    ]

    operations = [
        migrations.RunPython(apply_rebrand, reverse_rebrand),
    ]
