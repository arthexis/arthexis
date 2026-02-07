from django.db import migrations, models


def default_forwarded_calls():
    return [
        "RemoteStartTransaction",
        "RemoteStopTransaction",
        "RequestStartTransaction",
        "RequestStopTransaction",
        "GetTransactionStatus",
        "GetDiagnostics",
        "ChangeAvailability",
        "ChangeConfiguration",
        "DataTransfer",
        "Reset",
        "TriggerMessage",
        "ReserveNow",
        "CancelReservation",
        "ClearCache",
        "UnlockConnector",
        "UpdateFirmware",
        "PublishFirmware",
        "UnpublishFirmware",
        "SetChargingProfile",
        "InstallCertificate",
        "DeleteCertificate",
        "CertificateSigned",
        "GetInstalledCertificateIds",
        "GetVariables",
        "SetVariables",
        "ClearChargingProfile",
        "SetMonitoringBase",
        "SetMonitoringLevel",
        "SetVariableMonitoring",
        "ClearVariableMonitoring",
        "GetMonitoringReport",
        "ClearDisplayMessage",
        "CustomerInformation",
        "GetBaseReport",
        "GetChargingProfiles",
        "GetDisplayMessages",
        "GetReport",
        "SetDisplayMessage",
        "SetNetworkProfile",
        "GetCompositeSchedule",
        "GetLocalListVersion",
        "GetLog",
    ]


class Migration(migrations.Migration):

    dependencies = [
        ("ocpp", "0017_charger_ownable_offline_notifications"),
    ]

    operations = [
        migrations.AddField(
            model_name="charger",
            name="forwarded_messages",
            field=models.JSONField(
                blank=True,
                help_text="OCPP messages the forwarding peer allows this charge point to emit.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="charger",
            name="forwarded_calls",
            field=models.JSONField(
                blank=True,
                help_text="OCPP commands the forwarding peer allows for this charge point.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="cpforwarder",
            name="forwarded_calls",
            field=models.JSONField(
                blank=True,
                default=default_forwarded_calls,
                help_text="Select the CSMS actions that should be accepted from the remote node.",
            ),
        ),
    ]
