"""Seed the OCPP Forwarder suite feature."""

from django.db import migrations


OCPP_FORWARDER_FEATURE_SLUG = "ocpp-forwarder"


def seed_ocpp_forwarder_suite_feature(apps, schema_editor):
    """Create or update the OCPP Forwarder suite feature definition."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Application = apps.get_model("app", "Application")

    ocpp_app = Application.objects.filter(name="ocpp").first()

    Feature.objects.update_or_create(
        slug=OCPP_FORWARDER_FEATURE_SLUG,
        defaults={
            "display": "OCPP Forwarder",
            "source": "mainstream",
            "summary": "Enable cross-node OCPP forwarding sessions for managed charge points.",
            "is_enabled": True,
            "main_app": ocpp_app,
            "node_feature": None,
            "admin_requirements": (
                "Allow administrators to configure CP forwarders and observe forwarding status."
            ),
            "public_requirements": "",
            "service_requirements": (
                "Maintain outbound forwarding websocket sessions, synchronize forwarding metadata, "
                "and relay allowed OCPP calls."
            ),
            "admin_views": [
                "admin:ocpp_cpforwarder_changelist",
                "admin:ocpp_charger_changelist",
            ],
            "public_views": [],
            "service_views": [
                "apps.ocpp.tasks.setup_forwarders",
                "apps.ocpp.forwarder.Forwarder",
            ],
            "code_locations": [
                "apps/ocpp/forwarder/__init__.py",
                "apps/ocpp/tasks/forwarding.py",
                "apps/ocpp/consumers/csms/transport.py",
                "apps/ocpp/consumers/base/connection_flow.py",
            ],
            "protocol_coverage": {
                "ocpp16": {
                    "cp_to_csms": [
                        "Authorize",
                        "BootNotification",
                        "DataTransfer",
                        "DiagnosticsStatusNotification",
                        "FirmwareStatusNotification",
                        "Heartbeat",
                        "MeterValues",
                        "StartTransaction",
                        "StatusNotification",
                        "StopTransaction",
                    ],
                    "csms_to_cp": [
                        "CancelReservation",
                        "ChangeAvailability",
                        "ChangeConfiguration",
                        "ClearCache",
                        "ClearChargingProfile",
                        "DataTransfer",
                        "GetCompositeSchedule",
                        "GetConfiguration",
                        "GetDiagnostics",
                        "GetLocalListVersion",
                        "RemoteStartTransaction",
                        "RemoteStopTransaction",
                        "ReserveNow",
                        "Reset",
                        "SendLocalList",
                        "SetChargingProfile",
                        "TriggerMessage",
                        "UnlockConnector",
                        "UpdateFirmware",
                    ],
                }
            },
        },
    )


def unseed_ocpp_forwarder_suite_feature(apps, schema_editor):
    """Delete the seeded OCPP Forwarder suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=OCPP_FORWARDER_FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0035_merge_20260307_2002"),
    ]

    operations = [
        migrations.RunPython(
            seed_ocpp_forwarder_suite_feature,
            unseed_ocpp_forwarder_suite_feature,
        ),
    ]
