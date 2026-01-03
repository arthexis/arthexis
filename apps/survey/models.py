from __future__ import annotations

import copy
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class SurveyTopic(Entity):
    """Topic that groups related survey questions."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


class QuestionType(models.TextChoices):
    BINARY = "binary", _("Binary")
    OPEN = "open", _("Open ended")


class SurveyQuestion(Entity):
    """Question that belongs to a topic."""

    topic = models.ForeignKey(
        SurveyTopic, on_delete=models.CASCADE, related_name="questions"
    )
    prompt = models.TextField()
    question_type = models.CharField(
        max_length=12, choices=QuestionType.choices, default=QuestionType.BINARY
    )
    yes_label = models.CharField(max_length=64, default=_("Yes"))
    no_label = models.CharField(max_length=64, default=_("No"))
    priority = models.IntegerField(default=0)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "position", "id"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.topic}: {self.prompt[:50]}"

    @property
    def labels(self) -> tuple[str, str]:
        return self.yes_label, self.no_label


class SurveyResult(Entity):
    """Aggregated survey answers stored as JSON for long term evolution."""

    topic = models.ForeignKey(
        SurveyTopic, on_delete=models.CASCADE, related_name="results"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    session_key = models.CharField(max_length=40, blank=True, default="")
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def answered_question_ids(self) -> set[int]:
        responses = self.data.get("responses", []) if isinstance(self.data, dict) else []
        return {resp.get("question_id") for resp in responses if "question_id" in resp}

    def _base_metadata(self) -> dict[str, Any]:
        identifiers: dict[str, Any] = {}
        if self.user_id:
            identifiers["user_id"] = self.user_id
        if self.session_key:
            identifiers["session_key"] = self.session_key
        return identifiers

    def record_answer(self, question: SurveyQuestion, answer: Any, request=None) -> None:
        """Store an answer and persist the JSON snapshot for the question."""

        payload = copy.deepcopy(self.data) if isinstance(self.data, dict) else {}
        payload.setdefault("topic", {"id": self.topic_id, "slug": self.topic.slug})
        identifiers = payload.setdefault("identifiers", self._base_metadata())

        if request is not None:
            if request.user.is_authenticated:
                identifiers.update(
                    {
                        "user_id": request.user.pk,
                        "username": request.user.get_username(),
                        "email": getattr(request.user, "email", ""),
                    }
                )
            if request.session.session_key:
                identifiers["session_key"] = request.session.session_key
            remote_addr = request.META.get("REMOTE_ADDR")
            user_agent = request.META.get("HTTP_USER_AGENT")
            if remote_addr:
                identifiers["remote_addr"] = remote_addr
            if user_agent:
                identifiers["user_agent"] = user_agent

        responses = payload.setdefault("responses", [])
        if any(resp.get("question_id") == question.id for resp in responses):
            self.data = payload
            self.save(update_fields=["data", "updated_at"])
            return

        responses.append(
            {
                "question_id": question.id,
                "question_type": question.question_type,
                "prompt": question.prompt,
                "priority": question.priority,
                "position": question.position,
                "answer": answer,
                "answered_at": timezone.now().isoformat(),
            }
        )
        self.data = payload
        self.save(update_fields=["data", "updated_at"])


__all__ = ["SurveyTopic", "SurveyQuestion", "SurveyResult", "QuestionType"]
