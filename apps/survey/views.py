from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .forms import SurveySubmissionForm
from .models import Survey, SurveyAnswer, SurveyResponse


def _respondent_filter(request):
    if request.user.is_authenticated:
        return {"user": request.user}

    if not request.session.session_key:
        request.session.create()

    return {"participant_token": request.session.session_key}


class SurveyDetailView(View):
    """Render and process a survey submission for the public site."""

    def get(self, request, pk):
        survey = get_object_or_404(Survey, pk=pk, is_active=True)
        if SurveyResponse.objects.filter(survey=survey, **_respondent_filter(request)).exists():
            messages.info(request, "You have already submitted this survey.")
            return redirect("survey:survey-list")

        form = SurveySubmissionForm(survey=survey)
        return render(request, "survey/survey_form.html", {"survey": survey, "form": form})

    def post(self, request, pk):
        survey = get_object_or_404(Survey, pk=pk, is_active=True)
        respondent = _respondent_filter(request)
        if SurveyResponse.objects.filter(survey=survey, **respondent).exists():
            messages.info(request, "You have already submitted this survey.")
            return redirect("survey:survey-list")

        form = SurveySubmissionForm(request.POST, survey=survey)
        if not form.is_valid():
            return render(request, "survey/survey_form.html", {"survey": survey, "form": form})

        response = SurveyResponse.objects.create(
            survey=survey,
            user=request.user if request.user.is_authenticated else None,
            participant_token=respondent.get("participant_token", ""),
        )

        for question in form.questions:
            field_name = SurveySubmissionForm._field_name(question.pk)
            submitted = form.cleaned_data[field_name]
            selected_ids = submitted if isinstance(submitted, list) else [submitted]
            selected_options = list(question.options.filter(pk__in=selected_ids))
            answer = SurveyAnswer.objects.create(response=response, question=question)
            answer.selected_options.set(selected_options)

        messages.success(request, "Thanks for completing the survey.")
        return redirect("survey:survey-list")


class SurveyListView(View):
    """List active surveys for the public site."""

    def get(self, request):
        responses = SurveyResponse.objects.filter(**_respondent_filter(request)).values_list(
            "survey_id", flat=True
        )
        surveys = Survey.objects.filter(is_active=True).exclude(pk__in=responses)
        return render(request, "survey/survey_list.html", {"surveys": surveys})
