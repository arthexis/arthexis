from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Survey(models.Model):
    """A survey campaign that can be published to users."""

    title = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "survey"
        ordering = ("title",)

    def __str__(self) -> str:
        return self.title


class SurveyQuestion(models.Model):
    """A question belonging to a survey."""

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="questions")
    prompt = models.CharField(max_length=300)
    allow_multiple = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "survey"
        ordering = ("display_order", "id")

    def __str__(self) -> str:
        return self.prompt


class SurveyOption(models.Model):
    """A selectable option for a survey question."""

    question = models.ForeignKey(SurveyQuestion, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=200)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "survey"
        ordering = ("display_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("question", "label"),
                name="survey_option_question_label_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.label


class SurveyResponse(models.Model):
    """A completed response from a user for a survey."""

    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="responses")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="survey_responses",
        null=True,
        blank=True,
    )
    participant_token = models.CharField(max_length=40, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "survey"
        ordering = ("-submitted_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("survey", "user"),
                condition=models.Q(user__isnull=False),
                name="survey_unique_authenticated_response",
            ),
            models.UniqueConstraint(
                fields=("survey", "participant_token"),
                condition=models.Q(participant_token__gt=""),
                name="survey_unique_participant_response",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(user__isnull=False) & models.Q(participant_token=""))
                    | (models.Q(user__isnull=True) & models.Q(participant_token__gt=""))
                ),
                name="survey_response_exactly_one_respondent",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.survey} by {self.user or 'anonymous'}"


class SurveyAnswer(models.Model):
    """A user's selected options for one survey question."""

    response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(SurveyQuestion, on_delete=models.CASCADE, related_name="answers")
    selected_options = models.ManyToManyField(SurveyOption, related_name="answers")

    class Meta:
        app_label = "survey"
        ordering = ("question__display_order", "question_id")
        constraints = [
            models.UniqueConstraint(
                fields=("response", "question"),
                name="survey_answer_response_question_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.question}"

    def clean(self) -> None:
        super().clean()
        if not self.response_id or not self.question_id:
            return

        if self.question.survey_id != self.response.survey_id:
            raise ValidationError({"question": "Question must belong to the response survey."})

        if not self.pk:
            return

        invalid_options = self.selected_options.exclude(question_id=self.question_id)
        if invalid_options.exists():
            raise ValidationError({"selected_options": "Selected options must belong to the answer question."})
