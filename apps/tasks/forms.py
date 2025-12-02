from django import forms

from apps.tasks.models import TaskCategory


class TaskCategoryAdminForm(forms.ModelForm):
    class Meta:
        model = TaskCategory
        fields = "__all__"
