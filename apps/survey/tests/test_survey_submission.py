import pytest
from django.contrib.auth import get_user_model
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
