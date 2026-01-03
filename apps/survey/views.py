from __future__ import annotations

import random
from typing import Iterable

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
import markdown

from .forms import SurveyResponseForm
from .models import QuestionType, SurveyQuestion, SurveyResult, SurveyTopic


def _select_next_question(questions: Iterable[SurveyQuestion]) -> SurveyQuestion | None:
    """Pick the highest priority question, randomizing within priority ties."""

    questions = list(questions)
    if not questions:
        return None

    highest_priority = max(q.priority for q in questions)
    candidates = [q for q in questions if q.priority == highest_priority]
    return random.choice(candidates)


def _get_or_create_result(request: HttpRequest, topic: SurveyTopic) -> SurveyResult:
    session_key = request.session.session_key
    if session_key is None:
        request.session.save()
        session_key = request.session.session_key

    result = None
    if request.user.is_authenticated:
        result = SurveyResult.objects.filter(topic=topic, user=request.user).first()
    if result is None and session_key:
        result = SurveyResult.objects.filter(topic=topic, session_key=session_key).first()

    if result is None:
        result = SurveyResult.objects.create(
            topic=topic,
            user=request.user if request.user.is_authenticated else None,
            session_key=session_key or "",
            data={"responses": [], "identifiers": {}},
        )
    elif not result.session_key and session_key:
        result.session_key = session_key
        result.save(update_fields=["session_key"])

    return result


def survey_topic(request: HttpRequest, topic_slug: str) -> HttpResponse:
    topic = get_object_or_404(SurveyTopic, slug=topic_slug)
    result = _get_or_create_result(request, topic)

    answered_ids = result.answered_question_ids()
    unanswered = topic.questions.exclude(id__in=answered_ids)

    question = _select_next_question(unanswered)
    if question is None:
        messages.success(request, "Thanks for completing this survey.")
        return render(request, "survey/completed.html", {"topic": topic, "result": result})

    if request.method == "POST":
        form = SurveyResponseForm(question, data=request.POST)
        if form.is_valid():
            answer = form.cleaned_answer()
            result.record_answer(question, answer, request=request)
            return redirect(reverse("survey:topic", kwargs={"topic_slug": topic.slug}))
    else:
        form = SurveyResponseForm(question)

    rendered_prompt = mark_safe(markdown.markdown(question.prompt))
    return render(
        request,
        "survey/topic.html",
        {
            "topic": topic,
            "question": question,
            "form": form,
            "rendered_prompt": rendered_prompt,
            "is_binary": question.question_type == QuestionType.BINARY,
        },
    )
