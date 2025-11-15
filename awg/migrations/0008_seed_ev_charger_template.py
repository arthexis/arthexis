from django.db import migrations

from django.db import migrations


def seed_ev_charger_template(apps, schema_editor):
    CalculatorTemplate = apps.get_model("awg", "CalculatorTemplate")
    CableSize = apps.get_model("awg", "CableSize")
    ConduitFill = apps.get_model("awg", "ConduitFill")

    CalculatorTemplate.objects.update_or_create(
        name="EV Charger",
        defaults={
            "description": "EV Charger - Residential charging for a single Electric Vehicle.",
            "amps": 40,
            "volts": 220,
            "material": "cu",
            "max_lines": 1,
            "phases": 2,
            "temperature": 60,
            "conduit": "emt",
            "ground": 1,
            "show_in_pages": True,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )

    cable_defaults = [
        {
            "awg_size": "10",
            "material": "cu",
            "dia_in": 0,
            "dia_mm": 0,
            "area_kcmil": 0,
            "area_mm2": 0,
            "k_ohm_km": 0.15,
            "k_ohm_kft": 0.15,
            "amps_60c": 35,
            "amps_75c": 90,
            "amps_90c": 100,
            "line_num": 1,
        },
        {
            "awg_size": "8",
            "material": "cu",
            "dia_in": 0,
            "dia_mm": 0,
            "area_kcmil": 0,
            "area_mm2": 0,
            "k_ohm_km": 0.4,
            "k_ohm_kft": 0.4,
            "amps_60c": 55,
            "amps_75c": 65,
            "amps_90c": 75,
            "line_num": 1,
        },
        {
            "awg_size": "6",
            "material": "cu",
            "dia_in": 0,
            "dia_mm": 0,
            "area_kcmil": 0,
            "area_mm2": 0,
            "k_ohm_km": 0.3,
            "k_ohm_kft": 0.3,
            "amps_60c": 95,
            "amps_75c": 105,
            "amps_90c": 115,
            "line_num": 1,
        },
    ]

    for item in cable_defaults:
        CableSize.objects.update_or_create(
            awg_size=item["awg_size"],
            material=item["material"],
            line_num=item["line_num"],
            defaults={
                **{k: v for k, v in item.items() if k not in {"awg_size", "material", "line_num"}},
                "is_seed_data": True,
                "is_deleted": False,
            },
        )

    ConduitFill.objects.update_or_create(
        trade_size="1",
        conduit="emt",
        defaults={
            "awg_10": 4,
            "awg_8": 4,
            "awg_6": 4,
            "is_seed_data": True,
            "is_deleted": False,
        },
    )


def unseed_ev_charger_template(apps, schema_editor):
    CalculatorTemplate = apps.get_model("awg", "CalculatorTemplate")
    CableSize = apps.get_model("awg", "CableSize")
    ConduitFill = apps.get_model("awg", "ConduitFill")
    try:
        template = CalculatorTemplate.objects.get(name="EV Charger")
    except CalculatorTemplate.DoesNotExist:
        return
    if template.is_seed_data:
        template.is_deleted = True
        template.save(update_fields=["is_deleted"])

    seeded_sizes = [
        ("10", "cu", 1),
        ("8", "cu", 1),
        ("6", "cu", 1),
    ]
    for awg_size, material, line_num in seeded_sizes:
        try:
            size = CableSize.objects.get(
                awg_size=awg_size, material=material, line_num=line_num
            )
        except CableSize.DoesNotExist:
            continue
        if size.is_seed_data:
            size.is_deleted = True
            size.save(update_fields=["is_deleted"])

    try:
        fill = ConduitFill.objects.get(trade_size="1", conduit="emt")
    except ConduitFill.DoesNotExist:
        return
    if fill.is_seed_data:
        fill.is_deleted = True
        fill.save(update_fields=["is_deleted"])


class Migration(migrations.Migration):
    dependencies = [
        ("awg", "0007_alter_powerlead_ip_address"),
    ]

    operations = [
        migrations.RunPython(seed_ev_charger_template, unseed_ev_charger_template),
    ]
