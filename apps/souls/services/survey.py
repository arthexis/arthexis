from __future__ import annotations

import hashlib
import json

from apps.survey.models import SurveyResponse


def _normalize_text(value: str) -> str:
    return " ".join((value or "").split()).strip().upper()


def normalize_survey_response(response: SurveyResponse) -> dict:
    answers_payload = []
    answers = (
        response.answers.select_related("question")
        .prefetch_related("selected_options")
        .all()
        .order_by("question__display_order", "question_id")
    )

    for answer in answers:
        selected_options = sorted(
            answer.selected_options.all(),
            key=lambda option: (option.display_order, option.id),
        )
        selected = [_normalize_text(option.label) for option in selected_options]
        answers_payload.append(
            {
                "allow_multiple": bool(answer.question.allow_multiple),
                "prompt": _normalize_text(answer.question.prompt),
                "selected": selected,
            }
        )

    return {"answers": answers_payload}


def digest_normalized_answers(normalized: dict) -> str:
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
