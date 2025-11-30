import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("pages", "0001_initial"),
        ("sites", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ChatSession",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="UUID")),
                        ("visitor_key", models.CharField(blank=True, db_index=True, max_length=64)),
                        ("whatsapp_number", models.CharField(blank=True, db_index=True, help_text="WhatsApp sender identifier associated with the chat session.", max_length=64)),
                        ("status", models.CharField(choices=[("open", "Open"), ("escalated", "Escalated"), ("closed", "Closed")], default="open", max_length=16)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("last_activity_at", models.DateTimeField(default=django.utils.timezone.now)),
                        ("last_visitor_activity_at", models.DateTimeField(blank=True, null=True)),
                        ("last_staff_activity_at", models.DateTimeField(blank=True, null=True)),
                        ("escalated_at", models.DateTimeField(blank=True, null=True)),
                        ("closed_at", models.DateTimeField(blank=True, null=True)),
                        ("site", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chat_sessions", to="sites.site")),
                        ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chat_sessions", to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        "db_table": "pages_chatsession",
                        "ordering": ["-last_activity_at", "-pk"],
                    },
                ),
                migrations.CreateModel(
                    name="ChatMessage",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("is_seed_data", models.BooleanField(default=False, editable=False)),
                        ("is_user_data", models.BooleanField(default=False, editable=False)),
                        ("is_deleted", models.BooleanField(default=False, editable=False)),
                        ("sender_display_name", models.CharField(blank=True, max_length=150)),
                        ("from_staff", models.BooleanField(default=False)),
                        ("body", models.TextField()),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("sender", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chat_messages", to=settings.AUTH_USER_MODEL)),
                        ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="chats.chatsession")),
                    ],
                    options={
                        "db_table": "pages_chatmessage",
                        "ordering": ["created_at", "pk"],
                    },
                ),
            ],
            database_operations=[],
        )
    ]
