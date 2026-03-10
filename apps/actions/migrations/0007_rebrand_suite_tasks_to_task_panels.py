from django.db import migrations


def _rename_permission_labels(apps, schema_editor, *, source_label, target_label):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    model_updates = {
        "stafftask": (f"{source_label} Task", f"{target_label} Panel"),
        "stafftaskpreference": (
            f"{source_label} Task Preference",
            f"{target_label} Panel Preference",
        ),
    }

    action_verbs = {"add", "change", "delete", "view"}

    for model_name, (old_model_label, new_model_label) in model_updates.items():
        content_type = ContentType.objects.filter(app_label="actions", model=model_name).first()
        if content_type is None:
            continue

        for codename, permission_id, name in Permission.objects.filter(
            content_type=content_type
        ).values_list("codename", "id", "name"):
            prefix, _, _ = codename.partition("_")
            if prefix not in action_verbs:
                continue
            if old_model_label not in name:
                continue

            Permission.objects.filter(id=permission_id).update(
                name=name.replace(old_model_label, new_model_label)
            )


def rename_permissions_to_task_panel_labels(apps, schema_editor):
    _rename_permission_labels(
        apps,
        schema_editor,
        source_label="Suite",
        target_label="Task",
    )


def rename_permissions_to_suite_task_labels(apps, schema_editor):
    _rename_permission_labels(
        apps,
        schema_editor,
        source_label="Task",
        target_label="Suite",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("actions", "0006_alter_stafftask_options_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="stafftask",
            options={
                "ordering": ("order", "label"),
                "verbose_name": "Task Panel",
                "verbose_name_plural": "Task Panels",
            },
        ),
        migrations.AlterModelOptions(
            name="stafftaskpreference",
            options={
                "ordering": ("task__order", "task__label"),
                "verbose_name": "Task Panel Preference",
                "verbose_name_plural": "Task Panel Preferences",
            },
        ),
        migrations.RunPython(
            rename_permissions_to_task_panel_labels,
            rename_permissions_to_suite_task_labels,
        ),
    ]
