from django.test import TestCase

from apps.maps.models import Location
from apps.tasks.forms import MaintenanceRequestForm
from apps.tasks.models import TaskCategory


class MaintenanceRequestFormTests(TestCase):
    """Regression coverage for maintenance request category rendering."""

    def test_category_field_excludes_blank_named_categories(self):
        """The category selector should not include empty-name category records."""
        TaskCategory.objects.create(name="")
        visible = TaskCategory.objects.create(name="Electrical")
        Location.objects.create(name="Main Yard")

        form = MaintenanceRequestForm()

        self.assertEqual(list(form.fields["category"].queryset), [visible])

    def test_category_field_has_no_empty_choice(self):
        """The category selector should not render an empty first option."""
        TaskCategory.objects.create(name="Mechanical")
        Location.objects.create(name="Main Yard")

        form = MaintenanceRequestForm()

        self.assertNotIn(("", "---------"), form.fields["category"].choices)
