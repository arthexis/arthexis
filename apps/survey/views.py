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


def _merge_results(source: SurveyResult, target: SurveyResult) -> SurveyResult:
    """Merge responses and identifiers from ``source`` into ``target``."""

    source_payload = source.data if isinstance(source.data, dict) else {}
    target_payload = target.data if isinstance(target.data, dict) else {}

    merged = {**source_payload.get("identifiers", {}), **target_payload.get("identifiers", {})}
    target_payload["identifiers"] = merged

    target_responses = target_payload.setdefault("responses", [])
    seen_ids = {resp.get("question_id") for resp in target_responses if "question_id" in resp}
    for resp in source_payload.get("responses", []):
        question_id = resp.get("question_id")
        if question_id is None or question_id in seen_ids:
            continue
        target_responses.append(resp)
        seen_ids.add(question_id)

    target.data = target_payload
    target.save(update_fields=["data", "updated_at"])
    return target


def _get_or_create_result(request: HttpRequest, topic: SurveyTopic) -> SurveyResult:
    session_key = request.session.session_key
    if session_key is None:
        request.session.save()
        session_key = request.session.session_key

    session_results = request.session.setdefault("survey_result_ids", {})
    session_result_id = session_results.get(str(topic.pk))

    session_result = None
    if session_result_id:
        session_result = SurveyResult.objects.filter(pk=session_result_id, topic=topic).first()
    if session_result is None and session_key:
        session_result = SurveyResult.objects.filter(topic=topic, session_key=session_key).first()

    user_result = None
    if request.user.is_authenticated:
        user_result = SurveyResult.objects.filter(topic=topic, user=request.user).first()

    if user_result and session_result and user_result.pk != session_result.pk:
        # Prefer the authenticated user's record and merge any in-progress answers
        user_result = _merge_results(session_result, user_result)
        session_result.delete()
        session_result = user_result

    result = user_result or session_result
    if result is None:
        result = SurveyResult.objects.create(
            topic=topic,
            user=request.user if request.user.is_authenticated else None,
            session_key=session_key or "",
            data={"responses": [], "identifiers": {}},
        )
    else:
        updated_fields: list[str] = []
        if request.user.is_authenticated and result.user_id is None:
            result.user = request.user
            updated_fields.append("user")
        if not result.session_key and session_key:
            result.session_key = session_key
            updated_fields.append("session_key")
        if updated_fields:
            updated_fields.append("updated_at")
            result.save(update_fields=updated_fields)

    if session_results.get(str(topic.pk)) != result.pk:
        session_results[str(topic.pk)] = result.pk
        request.session.modified = True

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
