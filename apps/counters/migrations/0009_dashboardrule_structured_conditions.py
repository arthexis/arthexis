from django.db import migrations, models


def migrate_legacy_conditions(apps, schema_editor):
    DashboardRule = apps.get_model("counters", "DashboardRule")
    from apps.counters.condition_structured import parse_legacy_condition

    for rule in DashboardRule.objects.filter(implementation="condition").iterator():
        condition_text = (rule.condition or "").strip()
        structured, error = parse_legacy_condition(condition_text)

        if structured is None:
            rule.condition_requires_triage = bool(condition_text and error)
            rule.condition_triage_note = error or ""
            if not condition_text:
                rule.condition_source = ""
                rule.condition_expected_text = ""
                rule.condition_expected_boolean = None
                rule.condition_expected_number = None
            rule.save(
                update_fields=[
                    "condition_expected_boolean",
                    "condition_expected_number",
                    "condition_expected_text",
                    "condition_requires_triage",
                    "condition_source",
                    "condition_triage_note",
                ]
            )
            continue

        rule.condition_source = structured.source
        rule.condition_operator = structured.operator
        rule.condition_expected_boolean = structured.expected_boolean
        rule.condition_expected_number = structured.expected_number
        rule.condition_expected_text = structured.expected_text
        rule.condition_requires_triage = False
        rule.condition_triage_note = ""
        rule.save(
            update_fields=[
                "condition_expected_boolean",
                "condition_expected_number",
                "condition_expected_text",
                "condition_operator",
                "condition_requires_triage",
                "condition_source",
                "condition_triage_note",
            ]
        )


def clear_structured_conditions(apps, schema_editor):
    DashboardRule = apps.get_model("counters", "DashboardRule")
    DashboardRule.objects.update(
        condition_expected_boolean=None,
        condition_expected_number=None,
        condition_expected_text="",
        condition_operator="=",
        condition_requires_triage=False,
        condition_source="",
        condition_triage_note="",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("counters", "0008_watchtower_aws_credentials_rule"),
    ]

    operations = [
        migrations.AddField(
            model_name="dashboardrule",
            name="condition_expected_boolean",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="dashboardrule",
            name="condition_expected_number",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="dashboardrule",
            name="condition_expected_text",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="dashboardrule",
            name="condition_operator",
            field=models.CharField(
                choices=[
                    ("=", "Equals"),
                    (">", "Greater than"),
                    (">=", "Greater than or equal"),
                    ("<", "Less than"),
                    ("<=", "Less than or equal"),
                    ("!=", "Not equal"),
                ],
                default="=",
                max_length=4,
            ),
        ),
        migrations.AddField(
            model_name="dashboardrule",
            name="condition_requires_triage",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="dashboardrule",
            name="condition_source",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="dashboardrule",
            name="condition_triage_note",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(
            migrate_legacy_conditions,
            reverse_code=clear_structured_conditions,
        ),
    ]
