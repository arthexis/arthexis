from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def remove_conflicting_webrequestsampler_owners(apps, schema_editor):
    web_request_sampler = apps.get_model("content", "WebRequestSampler")
    web_request_sampler.objects.filter(
        user__isnull=False,
        group__isnull=False,
    ).update(group=None)


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0004_webrequestsampler_webrequeststep_websample_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="webrequestsampler",
            old_name="owner",
            new_name="user",
        ),
        migrations.RenameField(
            model_name="webrequestsampler",
            old_name="security_group",
            new_name="group",
        ),
        migrations.AlterField(
            model_name="webrequestsampler",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text="User that owns this object.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="webrequestsampler",
            name="group",
            field=models.ForeignKey(
                blank=True,
                help_text="Security group that owns this object.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="+",
                to="groups.securitygroup",
            ),
        ),
        migrations.RunPython(
            code=remove_conflicting_webrequestsampler_owners,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="webrequestsampler",
            constraint=models.CheckConstraint(
                condition=(
                    (Q(user__isnull=True) & Q(group__isnull=True))
                    | (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="content_webrequestsampler_owner_exclusive",
            ),
        ),
    ]
