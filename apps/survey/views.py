from uuid import uuid4

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .forms import SurveySubmissionForm
from .models import Survey, SurveyAnswer, SurveyResponse

PARTICIPANT_TOKEN_SESSION_KEY = "survey_participant_token"


def _respondent_filter(request, *, create_token=False):
    if request.user.is_authenticated:
        return {"user": request.user}

    participant_token = request.session.get(PARTICIPANT_TOKEN_SESSION_KEY)
    if participant_token:
        return {"participant_token": participant_token}

    if create_token:
        participant_token = uuid4().hex
        request.session[PARTICIPANT_TOKEN_SESSION_KEY] = participant_token
        return {"participant_token": participant_token}

    return {"participant_token": ""}


class SurveyDetailView(View):
    """Render and process a survey submission for the public site."""

    def get(self, request, pk):
        survey = get_object_or_404(Survey, pk=pk, is_active=True)
        respondent = _respondent_filter(request)
        participant_token = respondent.get("participant_token", "")
        if request.user.is_authenticated or participant_token:
            if SurveyResponse.objects.filter(survey=survey, **respondent).exists():
                messages.info(request, "You have already submitted this survey.")
                return redirect("survey:survey-list")

        form = SurveySubmissionForm(survey=survey)
        return render(request, "survey/survey_form.html", {"survey": survey, "form": form})

    def post(self, request, pk):
        survey = get_object_or_404(Survey, pk=pk, is_active=True)
        respondent = _respondent_filter(request, create_token=True)
        if SurveyResponse.objects.filter(survey=survey, **respondent).exists():
            messages.info(request, "You have already submitted this survey.")
            return redirect("survey:survey-list")

        form = SurveySubmissionForm(request.POST, survey=survey)
        if not form.is_valid():
            return render(request, "survey/survey_form.html", {"survey": survey, "form": form})

        try:
            with transaction.atomic():
                response = SurveyResponse.objects.create(
                    survey=survey,
                    user=request.user if request.user.is_authenticated else None,
                    participant_token=respondent.get("participant_token", ""),
                )

                for question in form.questions:
                    field_name = SurveySubmissionForm._field_name(question.pk)
                    submitted = form.cleaned_data[field_name]
                    selected_ids = submitted if isinstance(submitted, list) else [submitted]
                    question_options = list(question.options.all())
                    selected_options = [opt for opt in question_options if str(opt.pk) in selected_ids]
                    answer = SurveyAnswer.objects.create(response=response, question=question)
                    answer.selected_options.set(selected_options)
        except IntegrityError:
            if SurveyResponse.objects.filter(survey=survey, **respondent).exists():
                messages.info(request, "You have already submitted this survey.")
                return redirect("survey:survey-list")
            raise

        messages.success(request, "Thanks for completing the survey.")
        return redirect("survey:survey-list")


class SurveyListView(View):
    """List active surveys for the public site."""

    def get(self, request):
        respondent = _respondent_filter(request)
        participant_token = respondent.get("participant_token", "")
        if request.user.is_authenticated or participant_token:
            responses = SurveyResponse.objects.filter(**respondent).values_list("survey_id", flat=True)
            surveys = Survey.objects.filter(is_active=True).exclude(pk__in=responses)
        else:
            surveys = Survey.objects.filter(is_active=True)
        return render(request, "survey/survey_list.html", {"surveys": surveys})
