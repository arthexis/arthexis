from django.db import migrations


def _infer_key_type(public_key: str) -> str:
    normalized_key = (public_key or "").strip()
    return "x25519" if normalized_key.startswith("x25519:") else "rsa-bootstrap"


def _reclassify_transport_key_types(apps, schema_editor):
    node_key_material_model = apps.get_model("netmesh", "NodeKeyMaterial")
    for key_material in node_key_material_model.objects.all().only("id", "public_key", "key_type"):
        expected_key_type = _infer_key_type(key_material.public_key)
        if key_material.key_type == expected_key_type:
            continue
        key_material.key_type = expected_key_type
        key_material.save(update_fields=["key_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("netmesh", "0008_remove_nodekeymaterial_netmesh_node_single_active_key_and_more"),
    ]

    operations = [
        migrations.RunPython(
            _reclassify_transport_key_types,
            migrations.RunPython.noop,
        ),
    ]
