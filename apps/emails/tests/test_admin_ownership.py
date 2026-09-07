def test_email_outbox_admin_is_owned_by_emails_app():
    from apps.emails.admin_outbox import EmailOutboxAdmin
    from apps.nodes.admin.email_outbox_admin import EmailOutboxAdmin as LegacyAdmin

    assert EmailOutboxAdmin.__module__ == "apps.emails.admin_outbox"
    assert LegacyAdmin is EmailOutboxAdmin


def test_mailbox_admin_classes_are_owned_by_emails_app():
    from apps.core.admin.emails import EmailCollectorAdmin as LegacyCollectorAdmin
    from apps.core.admin.emails import EmailInboxAdmin as LegacyInboxAdmin
    from apps.emails.admin_impl.emails import EmailCollectorAdmin, EmailInboxAdmin

    assert EmailCollectorAdmin.__module__ == "apps.emails.admin_impl.emails"
    assert EmailInboxAdmin.__module__ == "apps.emails.admin_impl.emails"
    assert LegacyCollectorAdmin is EmailCollectorAdmin
    assert LegacyInboxAdmin is EmailInboxAdmin
