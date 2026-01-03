import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.survey.models import QuestionType, SurveyQuestion, SurveyResult, SurveyTopic

pytestmark = pytest.mark.django_db


def _create_topic_with_questions():
    topic = SurveyTopic.objects.create(name="Product Feedback", slug="feedback")
    low_priority = SurveyQuestion.objects.create(
        topic=topic,
        prompt="Tell us more about your experience.",
        question_type=QuestionType.OPEN,
        priority=1,
        position=1,
    )
    high_priority = SurveyQuestion.objects.create(
        topic=topic,
        prompt="Would you recommend us?",
        question_type=QuestionType.BINARY,
        priority=5,
        position=0,
    )
    return topic, high_priority, low_priority


def test_highest_priority_question_served_first(client, monkeypatch):
    topic, high_priority, _ = _create_topic_with_questions()
    url = reverse("survey:topic", kwargs={"topic_slug": topic.slug})

    monkeypatch.setattr("apps.survey.views.random.choice", lambda seq: seq[0])
    response = client.get(url)

    assert response.status_code == 200
    assert response.context["question"].id == high_priority.id


def test_answers_are_recorded_and_follow_up_question_is_shown(client, monkeypatch):
    user = get_user_model().objects.create_user(
        username="survey-user", email="survey@example.com", password="pass"
    )
    client.force_login(user)

    topic, first_question, second_question = _create_topic_with_questions()
    url = reverse("survey:topic", kwargs={"topic_slug": topic.slug})

    monkeypatch.setattr("apps.survey.views.random.choice", lambda seq: seq[0])

    response = client.post(url, {"question_id": first_question.id, "answer": "yes"})
    assert response.status_code == 302

    result = SurveyResult.objects.get(topic=topic, user=user)
    assert result.data["responses"][0]["question_id"] == first_question.id
    assert result.data["responses"][0]["answer"] is True
    assert result.data["identifiers"]["user_id"] == user.pk

    follow_up = client.get(url)
    assert follow_up.context["question"].id == second_question.id

    client.post(url, {"question_id": second_question.id, "answer": "Great!"})
    completion = client.get(url)
    assert completion.status_code == 200
    assert "Thanks for completing this survey." in completion.content.decode()


def test_new_questions_are_served_after_completion(client, monkeypatch):
    topic, first_question, second_question = _create_topic_with_questions()
    url = reverse("survey:topic", kwargs={"topic_slug": topic.slug})

    monkeypatch.setattr("apps.survey.views.random.choice", lambda seq: seq[0])

    client.post(url, {"question_id": first_question.id, "answer": "yes"})
    client.post(url, {"question_id": second_question.id, "answer": "All good"})

    # After completion, add another question and ensure it is shown on revisit
    new_question = SurveyQuestion.objects.create(
        topic=topic,
        prompt="How did you hear about us?",
        question_type=QuestionType.OPEN,
        priority=3,
        position=2,
    )

    revisit = client.get(url)
    assert revisit.status_code == 200
    assert revisit.context["question"].id == new_question.id

    result = SurveyResult.objects.get(topic=topic)
    answered_ids = result.answered_question_ids()
    assert new_question.id not in answered_ids
    assert first_question.id in answered_ids
    assert second_question.id in answered_ids
