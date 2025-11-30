from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pages", "0001_initial"),
        ("chats", "0001_initial"),
        ("odoo", "0002_chat_bridge"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="ChatMessage"),
                migrations.DeleteModel(name="ChatSession"),
                migrations.DeleteModel(name="OdooChatBridge"),
                migrations.DeleteModel(name="WhatsAppChatBridge"),
            ],
            database_operations=[],
        )
    ]
