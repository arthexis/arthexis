"""Seed the WhatsApp Chat Bridge suite feature."""

from django.db import migrations


FEATURE_SLUG = "whatsapp-chat-bridge"
MAIN_APP_NAME = "meta"


def seed_whatsapp_chat_bridge_suite_feature(apps, schema_editor):
    """Create or update the WhatsApp Chat Bridge suite feature."""

    del schema_editor
    Application = apps.get_model("app", "Application")
    Feature = apps.get_model("features", "Feature")

    main_app = Application.objects.filter(name=MAIN_APP_NAME).first()
    Feature.objects.update_or_create(
        slug=FEATURE_SLUG,
        defaults={
            "display": "WhatsApp Chat Bridge",
            "summary": (
                "Controls WhatsApp webhook intake and bridge/session activity. Disabled "
                "uses soft mode: Arthexis still accepts webhook payloads for audit/debug "
                "handling where supported, but it does not create WhatsApp bridge traffic "
                "or chat session messages."
            ),
            "is_enabled": True,
            "main_app": main_app,
            "node_feature": None,
            "admin_requirements": (
                "Operators manage the feature in Suite Features. When disabled, Meta "
                "webhook endpoints continue to accept inbound payloads for audit visibility "
                "while bridge delivery and chat session creation stay blocked. Admin "
                "screens for WhatsApp webhooks and messages should surface that disable "
                "contract clearly."
            ),
            "public_requirements": "No public-facing requirements.",
            "service_requirements": (
                "WhatsApp webhook entry points should remain consistent: disabled mode "
                "returns accepted-without-bridge activity, persists Meta webhook message "
                "payloads for audit/debug, and prevents new WhatsApp bridge or chat "
                "session message creation, including traffic aimed at existing sessions."
            ),
            "admin_views": [
                "admin:features_feature_changelist",
                "admin:meta_whatsappwebhook_changelist",
                "admin:meta_whatsappwebhookmessage_changelist",
            ],
            "public_views": [],
            "service_views": [
                "meta:whatsapp-webhook",
                "pages:whatsapp-webhook",
            ],
            "code_locations": [
                "apps/meta/admin.py",
                "apps/meta/models.py",
                "apps/meta/views.py",
                "apps/sites/views/management.py",
            ],
            "protocol_coverage": {},
            "metadata": {},
            "source": "mainstream",
        },
    )


def unseed_whatsapp_chat_bridge_suite_feature(apps, schema_editor):
    """Delete the seeded WhatsApp Chat Bridge suite feature."""

    del schema_editor
    Feature = apps.get_model("features", "Feature")
    Feature.objects.filter(slug=FEATURE_SLUG).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("features", "0048_remove_development_blog_feature"),
    ]

    operations = [
        migrations.RunPython(
            seed_whatsapp_chat_bridge_suite_feature,
            reverse_code=unseed_whatsapp_chat_bridge_suite_feature,
        ),
    ]
