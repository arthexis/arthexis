import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client
from django.urls import reverse

from apps.survey.models import (
    Survey,
    SurveyAnswer,
    SurveyOption,
    SurveyQuestion,
    SurveyResponse,
)


@pytest.mark.django_db
def test_authenticated_user_can_submit_multi_option_survey(client):
    user = get_user_model().objects.create_user(username="survey-user", password="password123")
    survey = Survey.objects.create(title="Station Feedback", is_active=True)
    single_question = SurveyQuestion.objects.create(
        survey=survey,
        prompt="How satisfied are you?",
        allow_multiple=False,
        display_order=1,
    )
    multi_question = SurveyQuestion.objects.create(
        survey=survey,
        prompt="What should improve?",
        allow_multiple=True,
        display_order=2,
    )

    single_option = SurveyOption.objects.create(question=single_question, label="Very satisfied")
    multi_option_a = SurveyOption.objects.create(question=multi_question, label="Pricing")
    multi_option_b = SurveyOption.objects.create(question=multi_question, label="App UX")

    assert client.login(username="survey-user", password="password123")

    response = client.post(
        reverse("survey:survey-detail", kwargs={"pk": survey.pk}),
        data={
            f"question_{single_question.pk}": str(single_option.pk),
            f"question_{multi_question.pk}": [str(multi_option_a.pk), str(multi_option_b.pk)],
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("survey:survey-list")

    survey_response = SurveyResponse.objects.get(survey=survey, user=user)
    answers = SurveyAnswer.objects.filter(response=survey_response).order_by("question__display_order")

    assert answers.count() == 2
    assert list(answers[0].selected_options.values_list("id", flat=True)) == [single_option.id]
    assert set(answers[1].selected_options.values_list("id", flat=True)) == {multi_option_a.id, multi_option_b.id}


@pytest.mark.django_db
def test_anonymous_user_can_submit_public_survey(client):
    survey = Survey.objects.create(title="Public Feedback", is_active=True)
    question = SurveyQuestion.objects.create(
        survey=survey,
        prompt="How likely are you to recommend us?",
        allow_multiple=False,
        display_order=1,
    )
    option = SurveyOption.objects.create(question=question, label="Very likely")

    response = client.post(
        reverse("survey:survey-detail", kwargs={"pk": survey.pk}),
        data={f"question_{question.pk}": str(option.pk)},
    )

    assert response.status_code == 302
    stored_response = SurveyResponse.objects.get(survey=survey)
    assert stored_response.user is None
    assert stored_response.participant_token


@pytest.mark.django_db
def test_duplicate_submission_does_not_create_extra_responses(client):
    user = get_user_model().objects.create_user(username="duplicate-user", password="password123")
    auth_survey = Survey.objects.create(title="Authenticated Duplicate", is_active=True)
    auth_question = SurveyQuestion.objects.create(
        survey=auth_survey,
        prompt="How was your charging session?",
        allow_multiple=False,
        display_order=1,
    )
    auth_option = SurveyOption.objects.create(question=auth_question, label="Great")

    assert client.login(username="duplicate-user", password="password123")
    auth_payload = {f"question_{auth_question.pk}": str(auth_option.pk)}
    auth_url = reverse("survey:survey-detail", kwargs={"pk": auth_survey.pk})

    first_auth = client.post(auth_url, data=auth_payload)
    second_auth = client.post(auth_url, data=auth_payload)

    assert first_auth.status_code == 302
    assert second_auth.status_code == 302
    assert SurveyResponse.objects.filter(survey=auth_survey, user=user).count() == 1
    assert SurveyAnswer.objects.filter(response__survey=auth_survey).count() == 1

    anon_survey = Survey.objects.create(title="Anonymous Duplicate", is_active=True)
    anon_question = SurveyQuestion.objects.create(
        survey=anon_survey,
        prompt="Would you return?",
        allow_multiple=False,
        display_order=1,
    )
    anon_option = SurveyOption.objects.create(question=anon_question, label="Yes")

    anonymous_client = Client()
    anon_payload = {f"question_{anon_question.pk}": str(anon_option.pk)}
    anon_url = reverse("survey:survey-detail", kwargs={"pk": anon_survey.pk})

    first_anon = anonymous_client.post(anon_url, data=anon_payload)
    second_anon = anonymous_client.post(anon_url, data=anon_payload)

    assert first_anon.status_code == 302
    assert second_anon.status_code == 302
    assert SurveyResponse.objects.filter(survey=anon_survey, user=None).count() == 1
    assert SurveyAnswer.objects.filter(response__survey=anon_survey).count() == 1


@pytest.mark.django_db
def test_answer_selected_options_validation_enforced_on_set():
    survey = Survey.objects.create(title="Validation Survey", is_active=True)
    single_question = SurveyQuestion.objects.create(
        survey=survey,
        prompt="Pick one option",
        allow_multiple=False,
        display_order=1,
    )
    other_question = SurveyQuestion.objects.create(
        survey=survey,
        prompt="Different question",
        allow_multiple=True,
        display_order=2,
    )
    option_a = SurveyOption.objects.create(question=single_question, label="A")
    option_b = SurveyOption.objects.create(question=single_question, label="B")
    invalid_option = SurveyOption.objects.create(question=other_question, label="Wrong question")

    response = SurveyResponse.objects.create(survey=survey, participant_token="anon-token")
    answer = SurveyAnswer.objects.create(response=response, question=single_question)

    with pytest.raises(ValidationError), transaction.atomic():
        answer.selected_options.set([option_a, option_b])

    with pytest.raises(ValidationError), transaction.atomic():
        answer.selected_options.set([invalid_option])

    answer.selected_options.set([option_a])
    answer.full_clean()
