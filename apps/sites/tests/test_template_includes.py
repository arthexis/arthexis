from pathlib import Path

from django.template import Context, TemplateDoesNotExist

from apps.sites.admin.story_admin import UserStoryAdmin
from apps.sites.models import UserStory
from apps.sites.templatetags.admin_extras import include_if_exists


def test_shortcuts_include_is_optional_in_shared_templates():
    for template_path in (
        Path("apps/sites/templates/admin/base_site.html"),
        Path("apps/sites/templates/pages/base.html"),
    ):
        template_text = template_path.read_text(encoding="utf-8")
        assert (
            '{% include_if_exists "shortcuts/includes/client_shortcuts_setup.html" %}'
            in template_text
        )


def test_include_if_exists_returns_empty_string_when_template_is_missing(monkeypatch):
    def raise_missing(_template_name):
        raise TemplateDoesNotExist("shortcuts/includes/client_shortcuts_setup.html")

    monkeypatch.setattr("apps.sites.templatetags.admin_extras.loader.get_template", raise_missing)

    assert include_if_exists(Context({}), "shortcuts/includes/client_shortcuts_setup.html") == ""


def test_feedback_widgets_only_render_attachment_upload_field():
    import re
    for template_path in (
        Path("apps/sites/templates/admin/includes/user_story_feedback.html"),
        Path("apps/sites/templates/pages/includes/public_feedback_widget.html"),
    ):
        template_text = template_path.read_text(encoding="utf-8")
        assert 'name="attachments"' in template_text
        assert not re.search(r'name=["\']screenshot["\']', template_text)


def test_user_story_admin_form_omits_screenshot_upload_field():
    assert "screenshot" not in UserStoryAdmin.fields
    assert "screenshot" not in UserStoryAdmin.readonly_fields


def test_user_story_model_omits_screenshot_field():
    assert "screenshot" not in {field.name for field in UserStory._meta.get_fields()}
