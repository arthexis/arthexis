from django.db import migrations

ROLE_DESCRIPTION = "Multi-User Cloud & Orchestration"
WATCHTOWER_COLOR = "#daa520"
DEFAULT_BADGE_COLOR = "#28a745"


def _move_m2m(through_model, from_role_id: int, to_role_id: int, related_field: str) -> None:
    """Move M2M links from ``from_role_id`` to ``to_role_id`` safely."""

    filter_kwargs = {"noderole_id": from_role_id}
    for relation in list(through_model.objects.filter(**filter_kwargs)):
        create_kwargs = {
            related_field: getattr(relation, related_field),
            "noderole_id": to_role_id,
        }
        through_model.objects.get_or_create(**create_kwargs)
        relation.delete()


def rename_constellation_to_watchtower(apps, schema_editor) -> None:
    NodeRole = apps.get_model("nodes", "NodeRole")
    Node = apps.get_model("nodes", "Node")
    Module = apps.get_model("pages", "Module")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    Reference = apps.get_model("core", "Reference")

    watchtower = NodeRole.objects.filter(name="Watchtower").first()
    constellation = NodeRole.objects.filter(name="Constellation").first()

    if constellation and watchtower:
        Node.objects.filter(role=watchtower).update(role=constellation)
        Module.objects.filter(node_role=watchtower).update(node_role=constellation)
        _move_m2m(NodeFeature.roles.through, watchtower.id, constellation.id, "nodefeature_id")
        _move_m2m(Reference.roles.through, watchtower.id, constellation.id, "reference_id")
        watchtower.delete()
        watchtower = None

    if constellation and not watchtower:
        constellation.name = "Watchtower"
        constellation.description = ROLE_DESCRIPTION
        constellation.save(update_fields=["name", "description"])
        watchtower = constellation
    elif watchtower:
        if watchtower.description != ROLE_DESCRIPTION:
            watchtower.description = ROLE_DESCRIPTION
            watchtower.save(update_fields=["description"])
    else:
        return

    Node.objects.filter(
        role=watchtower,
        badge_color__in=["", DEFAULT_BADGE_COLOR],
    ).update(badge_color=WATCHTOWER_COLOR)


def rename_watchtower_to_constellation(apps, schema_editor) -> None:
    NodeRole = apps.get_model("nodes", "NodeRole")
    Node = apps.get_model("nodes", "Node")
    Module = apps.get_model("pages", "Module")
    NodeFeature = apps.get_model("nodes", "NodeFeature")
    Reference = apps.get_model("core", "Reference")

    constellation = NodeRole.objects.filter(name="Constellation").first()
    watchtower = NodeRole.objects.filter(name="Watchtower").first()

    if watchtower and constellation:
        Node.objects.filter(role=constellation).update(role=watchtower)
        Module.objects.filter(node_role=constellation).update(node_role=watchtower)
        _move_m2m(NodeFeature.roles.through, constellation.id, watchtower.id, "nodefeature_id")
        _move_m2m(Reference.roles.through, constellation.id, watchtower.id, "reference_id")
        constellation.delete()
        constellation = None

    if watchtower and not constellation:
        watchtower.name = "Constellation"
        watchtower.description = ROLE_DESCRIPTION
        watchtower.save(update_fields=["name", "description"])
        constellation = watchtower
    elif constellation:
        if constellation.description != ROLE_DESCRIPTION:
            constellation.description = ROLE_DESCRIPTION
            constellation.save(update_fields=["description"])
    else:
        return

    Node.objects.filter(
        role=constellation,
        badge_color__in=["", DEFAULT_BADGE_COLOR],
    ).update(badge_color=WATCHTOWER_COLOR)


class Migration(migrations.Migration):

    dependencies = [
        ("nodes", "0022_node_message_queue_length_pendingnetmessage"),
        ("pages", "0026_update_awg_landing_label"),
        ("core", "0074_rfid_reversed_uid"),
    ]

    operations = [
        migrations.RunPython(
            rename_constellation_to_watchtower,
            rename_watchtower_to_constellation,
        ),
    ]
