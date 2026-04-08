from django.contrib import admin

from .models import Survey, SurveyAnswer, SurveyOption, SurveyQuestion, SurveyResponse


class SurveyOptionInline(admin.TabularInline):
    model = SurveyOption
    extra = 1


class SurveyQuestionInline(admin.TabularInline):
    model = SurveyQuestion
    extra = 1


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title",)
    inlines = (SurveyQuestionInline,)


@admin.register(SurveyQuestion)
class SurveyQuestionAdmin(admin.ModelAdmin):
    list_display = ("prompt", "survey", "allow_multiple", "display_order")
    list_filter = ("allow_multiple", "survey")
    search_fields = ("prompt", "survey__title")
    inlines = (SurveyOptionInline,)


@admin.register(SurveyOption)
class SurveyOptionAdmin(admin.ModelAdmin):
    list_display = ("label", "question", "display_order")
    list_filter = ("question__survey",)
    search_fields = ("label", "question__prompt")


class SurveyAnswerInline(admin.TabularInline):
    model = SurveyAnswer
    extra = 0
    filter_horizontal = ("selected_options",)


@admin.register(SurveyResponse)
class SurveyResponseAdmin(admin.ModelAdmin):
    list_display = ("survey", "user", "submitted_at")
    list_filter = ("survey",)
    search_fields = ("survey__title", "user__username")
    inlines = (SurveyAnswerInline,)


@admin.register(SurveyAnswer)
class SurveyAnswerAdmin(admin.ModelAdmin):
    list_display = ("response", "question")
    list_filter = ("question__survey",)
    search_fields = ("question__prompt", "response__survey__title", "response__user__username")
    filter_horizontal = ("selected_options",)
