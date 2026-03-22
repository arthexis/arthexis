from django.conf import settings
from django.db import migrations, models


def _default_language_code() -> str:
    """Return the default language code used for translated restoration data."""

    return getattr(
        settings,
        "PARLER_DEFAULT_LANGUAGE_CODE",
        getattr(settings, "LANGUAGE_CODE", "en"),
    )


def _build_unique_slug(raw_slug: str, used_slugs: set[str]) -> str:
    """Return a slug that is unique within the in-memory ``used_slugs`` set."""

    base_slug = (raw_slug or "retired-web-sampler").strip("-") or "retired-web-sampler"
    candidate = base_slug[:100]
    suffix = 2
    while candidate in used_slugs:
        suffix_text = f"-{suffix}"
        candidate = f"{base_slug[: 100 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used_slugs.add(candidate)
    return candidate


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


def restore_sampler_relations(apps, schema_editor):
    """Recreate retired sampler rows so historical samples can roll back safely."""

    db_alias = schema_editor.connection.alias
    default_language = _default_language_code()
    WebRequestSampler = apps.get_model("content", "WebRequestSampler")
    WebRequestSamplerTranslation = apps.get_model(
        "content", "WebRequestSamplerTranslation"
    )
    WebRequestStep = apps.get_model("content", "WebRequestStep")
    WebRequestStepTranslation = apps.get_model("content", "WebRequestStepTranslation")
    WebSample = apps.get_model("content", "WebSample")
    WebSampleAttachment = apps.get_model("content", "WebSampleAttachment")

    used_sampler_slugs: set[str] = set()
    sampler_map: dict[tuple[int | None, str], int] = {}

    for sample in WebSample.objects.using(db_alias).all().order_by("pk"):
        sampler_key = (sample.legacy_sampler_id, sample.sampler_slug or "")
        sampler_id = sampler_map.get(sampler_key)
        if sampler_id is None:
            sampler_slug = _build_unique_slug(
                sample.sampler_slug or f"retired-web-sampler-{sample.pk}",
                used_sampler_slugs,
            )
            create_kwargs = {
                "slug": sampler_slug,
                "sampling_period_minutes": None,
                "last_sampled_at": sample.created_at,
                "owner_id": None,
                "security_group_id": None,
            }
            if sample.legacy_sampler_id is not None:
                create_kwargs["id"] = sample.legacy_sampler_id
            sampler = WebRequestSampler.objects.using(db_alias).create(**create_kwargs)
            WebRequestSamplerTranslation.objects.using(db_alias).create(
                master_id=sampler.pk,
                language_code=default_language,
                label=sample.sampler_label or sampler.slug.replace("-", " ").title(),
                description="Restored placeholder for retired generic web sampler.",
            )
            sampler_id = sampler.pk
            sampler_map[sampler_key] = sampler_id

        sample.sampler_id = sampler_id
        sample.save(update_fields=["sampler"])

    step_orders: dict[int, int] = {}
    used_step_slugs: dict[int, set[str]] = {}
    step_map: dict[tuple[int, int | None, str], int] = {}

    attachments = WebSampleAttachment.objects.using(db_alias).select_related(
        "sample", "content_sample"
    )
    for attachment in attachments.order_by("pk"):
        sampler_id = attachment.sample.sampler_id
        step_key = (sampler_id, attachment.legacy_step_id, attachment.step_slug or "")
        step_id = step_map.get(step_key)
        if step_id is None:
            sampler_used_slugs = used_step_slugs.setdefault(sampler_id, set())
            step_slug = _build_unique_slug(
                attachment.step_slug or f"retired-web-step-{attachment.pk}",
                sampler_used_slugs,
            )
            order = step_orders.get(sampler_id, 0)
            create_kwargs = {
                "order": order,
                "slug": step_slug,
                "name": attachment.step_name or step_slug.replace("-", " ").title(),
                "curl_command": "echo 'Retired generic sampler step placeholder'",
                "save_as_content": True,
                "attachment_kind": attachment.content_sample.kind,
                "sampler_id": sampler_id,
            }
            if attachment.legacy_step_id is not None:
                create_kwargs["id"] = attachment.legacy_step_id
            step = WebRequestStep.objects.using(db_alias).create(**create_kwargs)
            WebRequestStepTranslation.objects.using(db_alias).create(
                master_id=step.pk,
                language_code=default_language,
                name=attachment.step_name or create_kwargs["name"],
            )
            step_id = step.pk
            step_map[step_key] = step_id
            step_orders[sampler_id] = order + 1

        attachment.step_id = step_id
        attachment.save(update_fields=["step"])


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
        migrations.RunPython(copy_sampler_metadata, restore_sampler_relations),
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
