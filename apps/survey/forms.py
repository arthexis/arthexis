from django import forms

from .models import Survey, SurveyQuestion


class SurveySubmissionForm(forms.Form):
    """Render all survey questions as multiple-choice inputs."""

    def __init__(self, *args, survey: Survey, **kwargs):
        super().__init__(*args, **kwargs)
        self.survey = survey
        self.questions = list(survey.questions.prefetch_related("options").all())
        self.question_fields: list[tuple[SurveyQuestion, str]] = []

        for question in self.questions:
            choices = [(str(option.pk), option.label) for option in question.options.all()]
            field_class = (
                forms.MultipleChoiceField if question.allow_multiple else forms.ChoiceField
            )
            widget_class = (
                forms.CheckboxSelectMultiple if question.allow_multiple else forms.RadioSelect
            )
            field_name = self._field_name(question.pk)
            self.fields[field_name] = field_class(
                choices=choices,
                label=question.prompt,
                widget=widget_class,
            )
            self.question_fields.append((question, field_name))

    @staticmethod
    def _field_name(question_id: int) -> str:
        return f"question_{question_id}"
