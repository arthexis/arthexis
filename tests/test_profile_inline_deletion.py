from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory
from django.test import TestCase

from core.admin import PROFILE_INLINE_CONFIG, ProfileInlineFormSet
from core.models import AssistantProfile, EmailInbox, OdooProfile, ReleaseManager
from nodes.models import EmailOutbox


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
                "pypi_password": "pypi-pass",
                "pypi_url": "https://upload.pypi.org/legacy/",
            }
        if model is AssistantProfile:
            # ``issue_key`` handles creation and hashing; scopes get filled below.
            return {"scopes": ["assist"], "is_active": True}
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
                "pypi_password": "",
                "pypi_url": "",
                "user_datum": "",
            }
        if model is AssistantProfile:
            return {
                "user_key": "",
                "scopes": "",
                "is_active": "",
                "user_datum": "",
            }
        raise AssertionError(f"Unsupported profile model {model!r}")

    def _create_profile(self, model, user):
        initial = self._initial_profile_data(model)
        if model is AssistantProfile:
            profile, _plain = AssistantProfile.issue_key(user)
            update_fields = []
            for field, value in initial.items():
                setattr(profile, field, value)
                update_fields.append(field)
            if update_fields:
                profile.save(update_fields=update_fields)
            return profile
        return model.objects.create(user=user, **initial)

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

    def test_blank_submission_marks_profiles_for_deletion(self):
        profiles = [
            OdooProfile,
            EmailInbox,
            EmailOutbox,
            ReleaseManager,
            AssistantProfile,
        ]
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
                data = self._build_post_data(prefix, profile.pk, self._blank_form_values(model))
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
