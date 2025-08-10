from django.apps import apps

from emails.models import EmailPattern


def test_emailpattern_registered_under_post_office_label():
    assert EmailPattern._meta.app_label == "post_office"
    app_config = apps.get_app_config("post_office")
    assert EmailPattern in list(app_config.get_models())
