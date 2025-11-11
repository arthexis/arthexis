from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.widgets import OdooProductWidget
from teams.forms import TaskCategoryAdminForm
from teams.models import TaskCategory


class TaskCategoryModelTests(TestCase):
    def test_str_and_availability_label(self):
        category = TaskCategory.objects.create(
            name="Site Audit",
            availability=TaskCategory.AVAILABILITY_IMMEDIATE,
        )
        self.assertEqual(str(category), "Site Audit")
        self.assertEqual(category.availability_label(), "Immediate")

    def test_cost_validator_blocks_negative_values(self):
        category = TaskCategory(
            name="Hardware Install",
            cost=Decimal("-1.00"),
        )
        with self.assertRaises(ValidationError):
            category.full_clean()


class TaskCategoryAdminFormTests(TestCase):
    def test_odoo_product_widget_used(self):
        form = TaskCategoryAdminForm()
        self.assertIn("odoo_product", form.fields)
        self.assertIsInstance(form.fields["odoo_product"].widget, OdooProductWidget)
