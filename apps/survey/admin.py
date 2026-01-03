from django.contrib import admin

from .models import QuestionType, SurveyQuestion, SurveyResult, SurveyTopic


class SurveyQuestionInline(admin.TabularInline):
    model = SurveyQuestion
    extra = 0
    fields = ("prompt", "question_type", "priority", "position", "yes_label", "no_label")


@admin.register(SurveyTopic)
class SurveyTopicAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    list_display = ("name", "slug", "created_at", "updated_at")
    search_fields = ("name", "slug")
    inlines = [SurveyQuestionInline]


@admin.register(SurveyQuestion)
class SurveyQuestionAdmin(admin.ModelAdmin):
    list_display = (
        "prompt",
        "topic",
        "question_type",
        "priority",
        "position",
        "created_at",
    )
    list_filter = ("question_type", "topic")
    search_fields = ("prompt", "topic__name")


@admin.register(SurveyResult)
class SurveyResultAdmin(admin.ModelAdmin):
    list_display = ("topic", "user", "session_key", "created_at", "updated_at")
    search_fields = ("topic__name", "user__username", "session_key")
    readonly_fields = ("created_at", "updated_at", "data")
