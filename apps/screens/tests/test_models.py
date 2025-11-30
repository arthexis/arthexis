from django.test import TestCase

from apps.screens.models import DeviceScreen


class DeviceScreenTests(TestCase):
    def test_seed_lcd_screen_created(self):
        screen = DeviceScreen.objects.get(slug="lcd-1602")
        self.assertEqual(screen.columns, 16)
        self.assertEqual(screen.rows, 2)
        self.assertEqual(screen.category, DeviceScreen.Category.LCD)
        self.assertTrue(screen.is_seed_data)
        self.assertIn("16x2", str(screen))
