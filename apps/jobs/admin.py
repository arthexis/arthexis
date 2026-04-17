from django.contrib import admin

from .models import CVSubmission, JobPosting


@admin.register(JobPosting)
class JobPostingAdmin(admin.ModelAdmin):
    list_display = ("title", "team", "location", "is_public", "publish_at", "close_at")
    list_filter = ("is_public", "team")
    search_fields = ("title", "team", "location", "summary")


@admin.register(CVSubmission)
class CVSubmissionAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "job_posting", "created_at")
    list_filter = ("job_posting",)
    search_fields = ("full_name", "email", "phone")
