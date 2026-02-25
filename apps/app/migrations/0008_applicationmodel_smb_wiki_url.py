"""Backfill a Wikipedia reference URL for SMB server application models."""

from django.db import migrations

from apps.app.models import DEFAULT_MODEL_WIKI_URLS


APP_NAME = "smb"
MODEL_LABEL = "smb.SMBServer"
WIKI_URL = DEFAULT_MODEL_WIKI_URLS.get((APP_NAME, MODEL_LABEL))


def _get_smb_application(apps):
    """Return the SMB application record if it exists."""

    Application = apps.get_model("app", "Application")

    try:
        return Application.objects.get(name=APP_NAME)
    except Application.DoesNotExist:
        return None


def set_smb_wiki_url(apps, schema_editor):
    """Set the SMB server model's Wikipedia URL."""

    ApplicationModel = apps.get_model("app", "ApplicationModel")
    application = _get_smb_application(apps)
    if not application or not WIKI_URL:
        return

    ApplicationModel.objects.filter(
        application=application,
        label__iexact=MODEL_LABEL,
    ).update(wiki_url=WIKI_URL)


def unset_smb_wiki_url(apps, schema_editor):
    """Revert the SMB server model's Wikipedia URL update."""

    ApplicationModel = apps.get_model("app", "ApplicationModel")
    application = _get_smb_application(apps)
    if not application or not WIKI_URL:
        return

    ApplicationModel.objects.filter(
        application=application,
        label__iexact=MODEL_LABEL,
        wiki_url=WIKI_URL,
    ).update(wiki_url="")


class Migration(migrations.Migration):
    """Assign a canonical SMB Wikipedia URL to the SMBServer application model."""

    dependencies = [
        ("app", "0007_applicationmodel_ocpp_wiki_url"),
        ("smb", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(set_smb_wiki_url, reverse_code=unset_smb_wiki_url),
    ]
