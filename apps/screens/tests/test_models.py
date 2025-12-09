from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.screens.models import CharacterScreen, PixelScreen


class DeviceScreenTests(TestCase):
    def test_update_text_respects_refresh_rate(self):
        screen = CharacterScreen.objects.create(
            slug="unit",
            name="Unit Test Screen",
            skin="virtual",
            columns=2,
            rows=2,
            min_refresh_ms=100,
        )

        first_time = timezone.now()
        first = screen.update_text("A", received_at=first_time)
        self.assertTrue(first)
        self.assertEqual(screen.character_buffer, "A")

        too_soon = first_time + timedelta(milliseconds=50)
        second = screen.update_text("B", received_at=too_soon)
        self.assertFalse(second)
        self.assertEqual(screen.character_buffer, "A")

        later = first_time + timedelta(milliseconds=150)
        third = screen.update_text("C", received_at=later)
        self.assertTrue(third)
        self.assertEqual(screen.character_buffer, "C")

    def test_update_pixels_respects_refresh_rate(self):
        screen = PixelScreen.objects.create(
            slug="pixel",
            name="Pixel Screen",
            skin="virtual",
            columns=2,
            rows=2,
            min_refresh_ms=100,
        )

        first_time = timezone.now()
        first = screen.update_pixels([[1, 0], [0, 1]], received_at=first_time)
        self.assertTrue(first)
        self.assertEqual(list(screen.pixel_buffer), [1, 0, 0, 1])

        too_soon = first_time + timedelta(milliseconds=20)
        second = screen.update_pixels([[2, 2], [2, 2]], received_at=too_soon)
        self.assertFalse(second)
        self.assertEqual(list(screen.pixel_buffer), [1, 0, 0, 1])

        later = first_time + timedelta(milliseconds=150)
        third = screen.update_pixels([[3, 3], [3, 3]], received_at=later)
        self.assertTrue(third)
        self.assertEqual(list(screen.pixel_buffer), [3, 3, 3, 3])

    def test_update_pixels_accepts_bytes_like_payload(self):
        screen = PixelScreen.objects.create(
            slug="pixel-bytes",
            name="Pixel Screen Bytes",
            skin="virtual",
            columns=2,
            rows=2,
        )

        payload = bytes([1, 2, 3, 4])
        screen.update_pixels(payload)

        self.assertEqual(list(screen.pixel_buffer), [1, 2, 3, 4])
