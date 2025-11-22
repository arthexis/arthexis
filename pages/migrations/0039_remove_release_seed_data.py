from django.db import migrations


RELEASE_PATH = "/release/"


def delete_release_seed_entries(apps, schema_editor):
    Application = apps.get_model("pages", "Application")
    Module = apps.get_model("pages", "Module")
    Landing = apps.get_model("pages", "Landing")
    NodeRole = apps.get_model("nodes", "NodeRole")

    try:
        core_app = Application.objects.get(name="core")
    except Application.DoesNotExist:
        return

    terminal_role = NodeRole.objects.filter(name="Terminal").first()
    if not terminal_role:
        return

    release_modules = Module.objects.filter(
        application=core_app, node_role=terminal_role, path=RELEASE_PATH
    )

    for module in release_modules:
        Landing.objects.filter(module=module).delete()
        module.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0038_chatsession_whatsapp_number_whatsappchatbridge"),
        ("nodes", "0047_remove_roleconfigurationprofile_role_and_more"),
    ]

    operations = [migrations.RunPython(delete_release_seed_entries, migrations.RunPython.noop)]
