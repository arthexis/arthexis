from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory
from django.test import TestCase
from django.urls import reverse
import re

from core.admin import PROFILE_INLINE_CONFIG, ProfileInlineFormSet
from core.models import (
    EmailInbox,
    OdooProfile,
    OpenPayProfile,
    ReleaseManager,
    SecurityGroup as CoreSecurityGroup,
)
from teams.models import EmailOutbox
from teams.models import SecurityGroup as TeamsSecurityGroup


class ProfileInlineDeletionTests(TestCase):
    """Ensure profile inlines delete instances when inputs are cleared."""

    maxDiff = None

    def setUp(self):
        self.user_model = get_user_model()

    def _initial_profile_data(self, model):
        if model is OdooProfile:
            return {
                "host": "https://odoo.example.com",
                "database": "odoo",
                "username": "odoo-user",
                "password": "secret",
            }
        if model is EmailInbox:
            return {
                "username": "inbox@example.com",
                "host": "imap.example.com",
                "port": 993,
                "password": "secret",
                "protocol": EmailInbox.IMAP,
                "use_ssl": True,
            }
        if model is EmailOutbox:
            return {
                "host": "smtp.example.com",
                "port": 587,
                "username": "mailer@example.com",
                "password": "secret",
                "use_tls": True,
                "use_ssl": False,
                "from_email": "mailer@example.com",
            }
        if model is ReleaseManager:
            return {
                "pypi_username": "publisher",
                "pypi_token": "pypi-token",
                "github_token": "gh-token",
                "git_username": "release-user",
                "git_password": "git-token",
                "pypi_password": "pypi-pass",
                "pypi_url": "https://upload.pypi.org/legacy/",
                "secondary_pypi_url": "https://pypi.example.com/simple/",
            }
        raise AssertionError(f"Unsupported profile model {model!r}")

    def _blank_form_values(self, model):
        if model is OdooProfile:
            return {
                "host": "",
                "database": "",
                "username": "",
                "password": "",
                "user_datum": "",
            }
        if model is EmailInbox:
            return {
                "username": "",
                "host": "",
                "port": "",
                "password": "",
                "protocol": "",
                "user_datum": "",
            }
        if model is EmailOutbox:
            return {
                "host": "",
                "port": "",
                "username": "",
                "password": "",
                "from_email": "",
                "user_datum": "",
            }
        if model is ReleaseManager:
            return {
                "pypi_username": "",
                "pypi_token": "",
                "github_token": "",
                "git_username": "",
                "git_password": "",
                "pypi_password": "",
                "pypi_url": "",
                "secondary_pypi_url": "",
                "user_datum": "",
            }
        raise AssertionError(f"Unsupported profile model {model!r}")

    def _create_profile(self, model, owner, owner_field="user"):
        initial = self._initial_profile_data(model)
        kwargs = {owner_field: owner}
        kwargs.update(initial)
        return model.objects.create(**kwargs)

    def _build_post_data(self, prefix, instance_pk, blank_fields):
        data = {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "1",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1",
            f"{prefix}-0-id": str(instance_pk),
        }
        for field, value in blank_fields.items():
            data[f"{prefix}-0-{field}"] = value
        return data

    def test_openpay_inline_includes_default_processor_field(self):
        config = PROFILE_INLINE_CONFIG[OpenPayProfile]
        fieldsets = config.get("fieldsets", ())
        fields = [
            field
            for _, options in fieldsets
            for field in options.get("fields", ())
        ]
        self.assertIn("default_processor", fields)

        form = config["form"]()
        self.assertIn("default_processor", form.fields)

    def test_blank_submission_marks_profiles_for_deletion(self):
        profiles = [OdooProfile, EmailInbox, EmailOutbox, ReleaseManager]
        for index, model in enumerate(profiles, start=1):
            with self.subTest(model=model._meta.label_lower):
                user = self.user_model.objects.create_user(f"profile-owner-{index}")
                profile = self._create_profile(model, user)
                form_class = PROFILE_INLINE_CONFIG[model]["form"]
                formset_cls = inlineformset_factory(
                    self.user_model,
                    model,
                    form=form_class,
                    formset=ProfileInlineFormSet,
                    fk_name="user",
                    extra=1,
                    can_delete=True,
                    max_num=1,
                )
                prefix = f"{model._meta.model_name}_set"
                data = self._build_post_data(
                    prefix, profile.pk, self._blank_form_values(model)
                )
                formset = formset_cls(data, instance=user, prefix=prefix)
                self.assertTrue(
                    formset.is_valid(),
                    msg=formset.errors or formset.non_form_errors(),
                )
                self.assertTrue(
                    formset.forms[0].cleaned_data.get("DELETE"),
                    msg="Inline form was not marked for deletion",
                )
                formset.save()
                self.assertFalse(model.objects.filter(pk=profile.pk).exists())

    def test_blank_submission_marks_group_profiles_for_deletion(self):
        profiles = [OdooProfile, EmailInbox, EmailOutbox, ReleaseManager]
        for index, model in enumerate(profiles, start=1):
            with self.subTest(model=model._meta.label_lower):
                group = CoreSecurityGroup.objects.create(name=f"profile-group-{index}")
                profile = self._create_profile(model, group, owner_field="group")
                form_class = PROFILE_INLINE_CONFIG[model]["form"]
                formset_cls = inlineformset_factory(
                    CoreSecurityGroup,
                    model,
                    form=form_class,
                    formset=ProfileInlineFormSet,
                    fk_name="group",
                    extra=1,
                    can_delete=True,
                    max_num=1,
                )
                prefix = f"{model._meta.model_name}_set"
                data = self._build_post_data(
                    prefix, profile.pk, self._blank_form_values(model)
                )
                formset = formset_cls(data, instance=group, prefix=prefix)
                self.assertTrue(
                    formset.is_valid(),
                    msg=formset.errors or formset.non_form_errors(),
                )
                self.assertTrue(
                    formset.forms[0].cleaned_data.get("DELETE"),
                    msg="Inline form was not marked for deletion",
                )
                formset.save()
                self.assertFalse(model.objects.filter(pk=profile.pk).exists())


class ProfileInlineAdminTemplateTests(TestCase):
    """Ensure admin change views hide explicit delete controls for profiles."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_superuser(
            "inline-admin", password="not-used"
        )

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_user_change_view_hides_inline_delete_checkbox(self):
        owner = get_user_model().objects.create_user("inline-user")
        ReleaseManager.objects.create(user=owner)
        url = reverse("admin:teams_user_change", args=[owner.pk])
        response = self.client.get(url)
        content = response.content.decode()
        prefixes = [
            fs.formset.prefix
            for fs in response.context_data["inline_admin_formsets"]
            if fs.formset.prefix != "phone_numbers"
        ]
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertRegex(
                    content,
                    rf'name="{re.escape(prefix)}-0-DELETE"',
                    msg="Inline delete input should render as a hidden field on user change view",
                )
                self.assertNotRegex(
                    content,
                    rf'label[^>]+for="id_{re.escape(prefix)}-0-DELETE"',
                    msg="Inline delete label should not be rendered on user change view",
                )
            selector = f'[data-inline-prefix="{prefix}"] .inline-deletelink'
            self.assertTrue(
                selector in content,
                msg=f"Expected CSS selector {selector} in user change view output",
            )
            self.assertTrue(
                f'[data-inline-prefix="{prefix}"] .form-row.field-DELETE' in content,
                msg=f"Expected CSS to hide form rows for inline prefix {prefix}",
            )
            self.assertTrue(
                f'[data-inline-prefix="{prefix}"] .fieldBox.field-DELETE' in content,
                msg=f"Expected CSS to hide field boxes for inline prefix {prefix}",
            )

    def test_security_group_change_view_hides_inline_delete_checkbox(self):
        group = TeamsSecurityGroup.objects.create(name="Inline Group")
        ReleaseManager.objects.create(group=group)
        url = reverse("admin:teams_securitygroup_change", args=[group.pk])
        response = self.client.get(url)
        content = response.content.decode()
        prefixes = [
            fs.formset.prefix for fs in response.context_data["inline_admin_formsets"]
        ]
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertRegex(
                    content,
                    rf'name="{re.escape(prefix)}-0-DELETE"',
                    msg="Inline delete input should render as a hidden field on security group change view",
                )
                self.assertNotRegex(
                    content,
                    rf'label[^>]+for="id_{re.escape(prefix)}-0-DELETE"',
                    msg="Inline delete label should not be rendered on security group change view",
                )
            selector = f'[data-inline-prefix="{prefix}"] .inline-deletelink'
            self.assertTrue(
                selector in content,
                msg=f"Expected CSS selector {selector} in security group change view output",
            )
            self.assertTrue(
                f'[data-inline-prefix="{prefix}"] .form-row.field-DELETE' in content,
                msg=f"Expected CSS to hide form rows for inline prefix {prefix}",
            )
            self.assertTrue(
                f'[data-inline-prefix="{prefix}"] .fieldBox.field-DELETE' in content,
                msg=f"Expected CSS to hide field boxes for inline prefix {prefix}",
            )

    def test_security_group_add_view_includes_delete_hiding_styles(self):
        url = reverse("admin:teams_securitygroup_add")
        response = self.client.get(url)
        content = response.content.decode()
        prefixes = [
            fs.formset.prefix for fs in response.context_data["inline_admin_formsets"]
        ]
        for prefix in prefixes:
            selector = f'[data-inline-prefix="{prefix}"] .inline-deletelink'
            self.assertTrue(
                selector in content,
                msg=f"Expected CSS selector {selector} in security group add view output",
            )
            self.assertTrue(
                f'[data-inline-prefix="{prefix}"] .form-row.field-DELETE' in content,
                msg=f"Expected CSS to hide form rows for inline prefix {prefix}",
            )
            self.assertTrue(
                f'[data-inline-prefix="{prefix}"] .fieldBox.field-DELETE' in content,
                msg=f"Expected CSS to hide field boxes for inline prefix {prefix}",
            )
