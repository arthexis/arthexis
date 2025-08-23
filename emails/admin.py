from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html

from .models import EmailPattern


@admin.register(EmailPattern)
class EmailPatternAdmin(admin.ModelAdmin):
    list_display = ("name", "subject", "test_button")
    actions = ["test_patterns"]
    change_form_template = "admin/emails/emailpattern/change_form.html"

    permission_app_labels = ("emails",)

    def _user_has_perm(self, request, perm):
        model = self.model._meta.model_name
        return any(
            request.user.has_perm(f"{label}.{perm}_{model}")
            for label in self.permission_app_labels
        )

    def has_view_permission(self, request, obj=None):
        return self._user_has_perm(request, "view")

    def has_change_permission(self, request, obj=None):
        return self._user_has_perm(request, "change")

    def has_delete_permission(self, request, obj=None):
        return self._user_has_perm(request, "delete")

    def has_add_permission(self, request):
        return self._user_has_perm(request, "add")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:pattern_id>/test/",
                self.admin_site.admin_view(self.test_view),
                name="emails_emailpattern_test",
            ),
        ]
        return custom + urls

    def test_button(self, obj):  # pragma: no cover - simple html
        url = reverse("admin:emails_emailpattern_test", args=[obj.pk])
        return format_html('<a class="button" href="{}">Test</a>', url)

    test_button.short_description = "Test"

    def test_patterns(self, request, queryset):
        for pattern in queryset:
            try:
                matches = pattern.test()
                if matches:
                    self.message_user(request, f"{pattern.name}: {matches}")
                else:
                    self.message_user(
                        request,
                        f"{pattern.name}: no match",
                        level=messages.WARNING,
                    )
            except Exception as exc:  # pragma: no cover - external call
                self.message_user(
                    request, f"{pattern.name}: {exc}", level=messages.ERROR
                )

    test_patterns.short_description = "Test selected patterns"

    def test_view(self, request, pattern_id):
        pattern = self.get_object(request, pattern_id)
        try:
            matches = pattern.test()
            if matches:
                self.message_user(request, f"{matches}")
            else:
                self.message_user(
                    request, "No match found", level=messages.WARNING
                )
        except Exception as exc:  # pragma: no cover - external call
            self.message_user(request, str(exc), level=messages.ERROR)
        return redirect("..")

