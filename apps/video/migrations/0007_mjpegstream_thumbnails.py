from django.db import migrations, models
import django.db.models.deletion
class Migration(migrations.Migration):
    dependencies = [
        ("content", "0006_remove_webrequestsampler_content_webrequestsampler_owner_exclusive_and_more"),
        ("video", "0006_videodevice_name_slug"),
    ]

    operations = [
        migrations.AddField(
            model_name="mjpegstream",
            name="last_frame_sample",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mjpeg_frames",
                to="content.contentsample",
            ),
        ),
        migrations.AddField(
            model_name="mjpegstream",
            name="last_frame_captured_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mjpegstream",
            name="last_thumbnail_sample",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mjpeg_thumbnails",
                to="content.contentsample",
            ),
        ),
        migrations.AddField(
            model_name="mjpegstream",
            name="last_thumbnail_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mjpegstream",
            name="thumbnail_frequency",
            field=models.PositiveIntegerField(
                default=60,
                help_text="Seconds between automatic thumbnail captures even without active viewers.",
            ),
        ),
    ]
