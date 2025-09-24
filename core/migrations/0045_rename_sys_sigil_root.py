from django.db import migrations


def _rename_sys_to_conf(apps, schema_editor):
    SigilRoot = apps.get_model("core", "SigilRoot")
    conf_root = SigilRoot.objects.filter(prefix__iexact="CONF").first()
    for root in SigilRoot.objects.filter(prefix__iexact="SYS"):
        if conf_root and conf_root.pk != root.pk:
            updated_fields = []
            if conf_root.prefix != "CONF":
                conf_root.prefix = "CONF"
                updated_fields.append("prefix")
            if conf_root.context_type != "config":
                conf_root.context_type = "config"
                updated_fields.append("context_type")
            if updated_fields:
                conf_root.save(update_fields=updated_fields)
            root.delete()
        else:
            updated_fields = []
            if root.prefix != "CONF":
                root.prefix = "CONF"
                updated_fields.append("prefix")
            if root.context_type != "config":
                root.context_type = "config"
                updated_fields.append("context_type")
            if updated_fields:
                root.save(update_fields=updated_fields)
            conf_root = root


def _rename_conf_to_sys(apps, schema_editor):
    SigilRoot = apps.get_model("core", "SigilRoot")
    sys_root = SigilRoot.objects.filter(prefix__iexact="SYS").first()
    for root in SigilRoot.objects.filter(prefix__iexact="CONF"):
        if sys_root and sys_root.pk != root.pk:
            updated_fields = []
            if sys_root.prefix != "SYS":
                sys_root.prefix = "SYS"
                updated_fields.append("prefix")
            if sys_root.context_type != "config":
                sys_root.context_type = "config"
                updated_fields.append("context_type")
            if updated_fields:
                sys_root.save(update_fields=updated_fields)
            root.delete()
        else:
            updated_fields = []
            if root.prefix != "SYS":
                root.prefix = "SYS"
                updated_fields.append("prefix")
            if root.context_type != "config":
                root.context_type = "config"
                updated_fields.append("context_type")
            if updated_fields:
                root.save(update_fields=updated_fields)
            sys_root = root


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_todo_url_normalize_loopback"),
    ]

    operations = [
        migrations.RunPython(
            _rename_sys_to_conf, _rename_conf_to_sys
        ),
    ]
