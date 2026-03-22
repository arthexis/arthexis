from django.db import migrations, models


def copy_sampler_metadata(apps, schema_editor):
    """Copy sampler and step metadata onto historical sample records."""

    WebRequestSamplerTranslation = apps.get_model(
        "content", "WebRequestSamplerTranslation"
    )
    WebRequestStepTranslation = apps.get_model("content", "WebRequestStepTranslation")
    WebSample = apps.get_model("content", "WebSample")
    WebSampleAttachment = apps.get_model("content", "WebSampleAttachment")

    sampler_labels = {
        translation.master_id: translation.label
        for translation in WebRequestSamplerTranslation.objects.using(schema_editor.connection.alias)
        .order_by("master_id", "id")
    }
    step_names = {
        translation.master_id: translation.name
        for translation in WebRequestStepTranslation.objects.using(schema_editor.connection.alias)
        .order_by("master_id", "id")
    }

    for sample in WebSample.objects.using(schema_editor.connection.alias).select_related("sampler"):
        sampler = sample.sampler
        sample.legacy_sampler_id = sampler.pk
        sample.sampler_slug = sampler.slug
        sample.sampler_label = sampler_labels.get(sampler.pk, "")
        sample.save(
            update_fields=["legacy_sampler_id", "sampler_slug", "sampler_label"]
        )

    for attachment in WebSampleAttachment.objects.using(
        schema_editor.connection.alias
    ).select_related("step"):
        step = attachment.step
        if step is None:
            continue
        attachment.legacy_step_id = step.pk
        attachment.step_slug = step.slug
        attachment.step_name = step_names.get(step.pk, "")
        attachment.save(
            update_fields=["legacy_step_id", "step_slug", "step_name"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0008_alter_contentclassifiertranslation_master_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="websample",
            name="legacy_sampler_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="websample",
            name="sampler_label",
            field=models.CharField(blank=True, default="", max_length=150),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="websample",
            name="sampler_slug",
            field=models.SlugField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="websampleattachment",
            name="legacy_step_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="websampleattachment",
            name="step_name",
            field=models.CharField(blank=True, default="", max_length=150),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="websampleattachment",
            name="step_slug",
            field=models.SlugField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.RunPython(copy_sampler_metadata, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="websample",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Historical Web Sample",
                "verbose_name_plural": "Historical Web Samples",
            },
        ),
        migrations.AlterModelOptions(
            name="websampleattachment",
            options={
                "verbose_name": "Historical Web Sample Attachment",
                "verbose_name_plural": "Historical Web Sample Attachments",
            },
        ),
        migrations.RemoveField(
            model_name="websample",
            name="sampler",
        ),
        migrations.RemoveField(
            model_name="websampleattachment",
            name="step",
        ),
        migrations.DeleteModel(
            name="WebRequestStepTranslation",
        ),
        migrations.DeleteModel(
            name="WebRequestSamplerTranslation",
        ),
        migrations.DeleteModel(
            name="WebRequestStep",
        ),
        migrations.DeleteModel(
            name="WebRequestSampler",
        ),
    ]
