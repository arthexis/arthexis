def test_email_outbox_admin_is_owned_by_emails_app():
    from apps.emails.admin_outbox import EmailOutboxAdmin
    from apps.nodes.admin.email_outbox_admin import EmailOutboxAdmin as LegacyAdmin

    assert EmailOutboxAdmin.__module__ == "apps.emails.admin_outbox"
    assert LegacyAdmin is EmailOutboxAdmin
