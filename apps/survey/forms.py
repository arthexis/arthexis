from __future__ import annotations

from django import forms

from .models import QuestionType, SurveyQuestion


class SurveyResponseForm(forms.Form):
    """Render a single-question response form tailored to the question type."""

    question_id = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, question: SurveyQuestion, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.question = question
        self.fields["question_id"].initial = question.id

        if question.question_type == QuestionType.BINARY:
            yes, no = question.labels
            self.fields["answer"] = forms.ChoiceField(
                label="",
                choices=[("yes", yes), ("no", no)],
                widget=forms.RadioSelect,
            )
        else:
            self.fields["answer"] = forms.CharField(
                label="",
                widget=forms.Textarea(attrs={"rows": 4}),
            )

    def clean_question_id(self):
        value = self.cleaned_data["question_id"]
        if value != self.question.id:
            raise forms.ValidationError("Mismatched question")
        return value

    def cleaned_answer(self):
        answer = self.cleaned_data.get("answer")
        if self.question.question_type == QuestionType.BINARY:
            return answer == "yes"
        return answer
