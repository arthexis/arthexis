from django.apps import apps

from emails.models import EmailPattern


def test_emailpattern_registered_under_emails_label():
    assert EmailPattern._meta.app_label == "emails"
    app_config = apps.get_app_config("emails")
    assert EmailPattern in list(app_config.get_models())
