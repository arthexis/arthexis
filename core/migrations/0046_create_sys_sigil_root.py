from django.db import migrations


def _create_sys_root(apps, schema_editor):
    SigilRoot = apps.get_model("core", "SigilRoot")
    root = SigilRoot.objects.filter(prefix__iexact="SYS").first()
    if root:
        updates = []
        if root.prefix != "SYS":
            root.prefix = "SYS"
            updates.append("prefix")
        if root.context_type != "config":
            root.context_type = "config"
            updates.append("context_type")
        if updates:
            root.save(update_fields=updates)
        return
    SigilRoot.objects.create(prefix="SYS", context_type="config")


def _remove_sys_root(apps, schema_editor):
    SigilRoot = apps.get_model("core", "SigilRoot")
    SigilRoot.objects.filter(prefix__iexact="SYS").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_invitelead_sent_via_outbox"),
        ("core", "0045_rename_sys_sigil_root"),
    ]

    operations = [
        migrations.RunPython(_create_sys_root, _remove_sys_root),
    ]
