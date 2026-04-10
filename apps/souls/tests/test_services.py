from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cards.models import OfferingSoul
from apps.cards.soul import PACKAGE_MAX_BYTES
from apps.souls.models import SoulRegistrationSession
from apps.souls.services import build_soul_package, digest_normalized_answers, normalize_survey_response
from apps.survey.models import Survey, SurveyAnswer, SurveyOption, SurveyQuestion, SurveyResponse


class SoulServicesTests(TestCase):
    def setUp(self):
        self.survey = Survey.objects.create(title="Soul Registration", is_active=True)
        self.question = SurveyQuestion.objects.create(
            survey=self.survey,
            prompt="Axis",
            allow_multiple=True,
            display_order=1,
        )
        self.option_a = SurveyOption.objects.create(question=self.question, label="As Above", display_order=2)
        self.option_b = SurveyOption.objects.create(question=self.question, label="So Below", display_order=1)

    def test_normalize_survey_response_and_digest_stable(self):
        response = SurveyResponse.objects.create(survey=self.survey, participant_token="token-1")
        answer = SurveyAnswer.objects.create(response=response, question=self.question)
        answer.selected_options.set([self.option_a, self.option_b])

        normalized = normalize_survey_response(response)
        digest_one = digest_normalized_answers(normalized)
        digest_two = digest_normalized_answers(normalized)

        self.assertEqual(digest_one, digest_two)
        self.assertEqual(normalized["answers"][0]["selected"], ["SO BELOW", "AS ABOVE"])

    def test_build_soul_package_respects_size_budget(self):
        user = get_user_model().objects.create_user(username="soul-user", email="soul@example.com", password="x")
        response = SurveyResponse.objects.create(survey=self.survey, participant_token="token-2")
        answer = SurveyAnswer.objects.create(response=response, question=self.question)
        answer.selected_options.set([self.option_a])
        offering = OfferingSoul.objects.create(
            core_hash="a" * 64,
            package={
                "schema_version": "1.0",
                "core_hash": "a" * 64,
                "issuance_marker": "",
                "metadata": {"size_bytes": 2},
                "traits": {"structural": {}, "type_aware": {}},
            },
            structural_traits={},
            type_traits={},
        )
        registration = SoulRegistrationSession.objects.create(
            email="soul@example.com",
            offering_soul=offering,
            survey_response=response,
        )

        package, _, _, _ = build_soul_package(registration_session=registration, user=user)

        encoded = json.dumps(package, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), PACKAGE_MAX_BYTES)
