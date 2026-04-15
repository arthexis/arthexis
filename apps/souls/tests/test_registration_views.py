from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.cards.models import OfferingSoul
from apps.souls.models import Soul, SoulRegistrationSession
from apps.souls.services import build_soul_package
from apps.survey.models import Survey, SurveyAnswer, SurveyOption, SurveyQuestion, SurveyResponse


class SoulRegistrationViewsTests(TestCase):
    def test_register_start_uses_same_redirect_for_existing_and_new_email(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="existing-user", email="existing@example.com", password="x")
        offering = OfferingSoul.objects.create(
            core_hash="a" * 64,
            package={
                "schema_version": "1.0",
                "core_hash": "a" * 64,
                "issuance_marker": "start",
                "metadata": {"size_bytes": 2},
                "traits": {"structural": {}, "type_aware": {}},
            },
            structural_traits={},
            type_traits={},
        )
        survey = Survey.objects.create(title="Soul Seed Registration", is_active=True)
        response = SurveyResponse.objects.create(survey=survey, participant_token="existing-token")
        Soul.objects.create(
            user=user,
            offering_soul=offering,
            survey_response=response,
            soul_id="existing-soul-id",
            survey_digest="digest",
            package={"schema_version": "1.0"},
            email_hash="hash",
        )

        existing_response = self.client.post(
            reverse("souls:register_start"),
            data={"email": "existing@example.com"},
        )
        new_response = self.client.post(
            reverse("souls:register_start"),
            data={"email": "new@example.com"},
        )

        self.assertRedirects(existing_response, reverse("souls:register_offering"), fetch_redirect_response=False)
        self.assertRedirects(new_response, reverse("souls:register_offering"), fetch_redirect_response=False)
        self.assertEqual(
            SoulRegistrationSession.objects.filter(email="existing@example.com").count(),
            1,
        )
        self.assertEqual(
            SoulRegistrationSession.objects.filter(email="new@example.com").count(),
            1,
        )

    def test_register_verify_blocks_claim_when_existing_soul_id_differs(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="claimed-user", email="claimed@example.com", password="x")
        survey = Survey.objects.create(title="Soul Seed Registration", is_active=True)
        question = SurveyQuestion.objects.create(
            survey=survey,
            prompt="Axis",
            allow_multiple=False,
            display_order=1,
        )
        option = SurveyOption.objects.create(question=question, label="As Above", display_order=1)
        response = SurveyResponse.objects.create(survey=survey, participant_token="token-verify")
        answer = SurveyAnswer.objects.create(response=response, question=question)
        answer.selected_options.set([option])
        offering = OfferingSoul.objects.create(
            core_hash="c" * 64,
            package={
                "schema_version": "1.0",
                "core_hash": "c" * 64,
                "issuance_marker": "one",
                "metadata": {"size_bytes": 2},
                "traits": {"structural": {}, "type_aware": {}},
            },
            structural_traits={},
            type_traits={},
        )
        registration = SoulRegistrationSession.objects.create(
            email=user.email,
            offering_soul=offering,
            survey_response=response,
            state=SoulRegistrationSession.State.EMAIL_SENT,
        )

        token = "valid-token"
        registration.verification_token_hash = SoulRegistrationSession.digest_value(token)
        registration.save(update_fields=["verification_token_hash"])

        Soul.objects.create(
            user=user,
            offering_soul=offering,
            survey_response=response,
            soul_id="different-soul-id",
            survey_digest="digest",
            package={"schema_version": "1.0"},
            email_hash="hash",
        )

        verify_response = self.client.get(
            reverse("souls:register_verify", kwargs={"session_id": registration.id, "token": token})
        )

        self.assertRedirects(verify_response, reverse("souls:register_landing"), fetch_redirect_response=False)
        self.assertFalse(verify_response.wsgi_request.user.is_authenticated)
        registration.refresh_from_db()
        self.assertEqual(registration.state, SoulRegistrationSession.State.EMAIL_SENT)

    def test_register_verify_blocks_claim_when_email_matches_multiple_users(self):
        user_model = get_user_model()
        first_user = user_model.objects.create_user(username="dupe-a", email="dupe@example.com", password="x")
        user_model.objects.create_user(username="dupe-b", email="dupe@example.com", password="x")
        survey = Survey.objects.create(title="Soul Seed Registration", is_active=True)
        question = SurveyQuestion.objects.create(
            survey=survey,
            prompt="Axis",
            allow_multiple=False,
            display_order=1,
        )
        option = SurveyOption.objects.create(question=question, label="As Above", display_order=1)
        response = SurveyResponse.objects.create(survey=survey, participant_token="token-dup")
        answer = SurveyAnswer.objects.create(response=response, question=question)
        answer.selected_options.set([option])
        offering = OfferingSoul.objects.create(
            core_hash="e" * 64,
            package={
                "schema_version": "1.0",
                "core_hash": "e" * 64,
                "issuance_marker": "dupe",
                "metadata": {"size_bytes": 2},
                "traits": {"structural": {}, "type_aware": {}},
            },
            structural_traits={},
            type_traits={},
        )
        registration = SoulRegistrationSession.objects.create(
            email=first_user.email,
            offering_soul=offering,
            survey_response=response,
            state=SoulRegistrationSession.State.EMAIL_SENT,
        )
        token = "valid-token-dup"
        registration.verification_token_hash = SoulRegistrationSession.digest_value(token)
        registration.save(update_fields=["verification_token_hash"])

        verify_response = self.client.get(
            reverse("souls:register_verify", kwargs={"session_id": registration.id, "token": token})
        )

        self.assertRedirects(verify_response, reverse("souls:register_landing"), fetch_redirect_response=False)
        self.assertFalse(verify_response.wsgi_request.user.is_authenticated)
        self.assertFalse(Soul.objects.filter(user__email__iexact=registration.email).exists())
        registration.refresh_from_db()
        self.assertEqual(registration.state, SoulRegistrationSession.State.EMAIL_SENT)

    def test_register_verify_allows_claim_when_existing_soul_id_matches(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username="matching-user", email="matching@example.com", password="x")
        survey = Survey.objects.create(title="Soul Seed Registration", is_active=True)
        question = SurveyQuestion.objects.create(
            survey=survey,
            prompt="Axis",
            allow_multiple=False,
            display_order=1,
        )
        option = SurveyOption.objects.create(question=question, label="As Above", display_order=1)
        response = SurveyResponse.objects.create(survey=survey, participant_token="token-match")
        answer = SurveyAnswer.objects.create(response=response, question=question)
        answer.selected_options.set([option])
        offering = OfferingSoul.objects.create(
            core_hash="d" * 64,
            package={
                "schema_version": "1.0",
                "core_hash": "d" * 64,
                "issuance_marker": "same",
                "metadata": {"size_bytes": 2},
                "traits": {"structural": {}, "type_aware": {}},
            },
            structural_traits={},
            type_traits={},
        )
        registration = SoulRegistrationSession.objects.create(
            email=user.email,
            offering_soul=offering,
            survey_response=response,
            state=SoulRegistrationSession.State.EMAIL_SENT,
        )

        package, soul_id, survey_digest, email_hash = build_soul_package(registration_session=registration, user=user)
        Soul.objects.create(
            user=user,
            offering_soul=offering,
            survey_response=response,
            soul_id=soul_id,
            survey_digest=survey_digest,
            package=package,
            email_hash=email_hash,
        )

        token = "valid-token-match"
        registration.verification_token_hash = SoulRegistrationSession.digest_value(token)
        registration.save(update_fields=["verification_token_hash"])

        verify_response = self.client.get(
            reverse("souls:register_verify", kwargs={"session_id": registration.id, "token": token})
        )

        self.assertRedirects(verify_response, reverse("souls:me"), fetch_redirect_response=False)
        self.assertTrue(verify_response.wsgi_request.user.is_authenticated)
        registration.refresh_from_db()
        self.assertEqual(registration.state, SoulRegistrationSession.State.COMPLETED)
