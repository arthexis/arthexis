from datetime import timedelta

from django.test import TestCase

from apps.pyxel.models import PyxelUnavailableError, PyxelViewport
from apps.screens.models import PixelScreen


class DummyPyxel:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.buffer: list[list[int]] = []
        self.quit_called = False
        self.run_called = False

    def init(self, width, height, title=None, fps=None):  # pragma: no cover - trivial
        self.width = width
        self.height = height
        self.buffer = [[0 for _ in range(width)] for _ in range(height)]

    def pset(self, x, y, value):
        self.buffer[y][x] = value

    def pget(self, x, y):
        return self.buffer[y][x]

    def quit(self):
        self.quit_called = True

    def run(self, update, draw):  # pragma: no cover - interactive shim
        self.run_called = True
        update()
        draw()


PYXEL_SCRIPT = """

def update():
    pyxel.pset(0, 0, 5)


def draw():
    pyxel.pset(1, 1, 9)
"""


class PyxelViewportTests(TestCase):
    def setUp(self):
        self.viewport = PyxelViewport.objects.create(
            slug="viewport",
            name="Viewport",
            skin="virtual",
            columns=2,
            rows=2,
            pyxel_script=PYXEL_SCRIPT,
            pyxel_fps=30,
        )

    def test_render_bitmap_runs_script(self):
        pyxel = DummyPyxel()
        bitmap = self.viewport.render_bitmap(pyxel_module=pyxel)
        self.assertEqual(list(bitmap), [5, 0, 0, 9])
        self.assertEqual(list(self.viewport.pixel_buffer), [5, 0, 0, 9])
        self.assertTrue(pyxel.quit_called)

    def test_open_viewport_uses_run_loop(self):
        pyxel = DummyPyxel()
        self.viewport.open_viewport(pyxel_module=pyxel)
        self.assertTrue(pyxel.run_called)
        self.assertEqual(list(self.viewport.pixel_buffer), [5, 0, 0, 9])

    def test_update_pixels_respects_refresh_interval(self):
        pyxel = DummyPyxel()
        self.viewport.min_refresh_ms = 200
        self.viewport.save(update_fields=["min_refresh_ms"])

        first = self.viewport.render_bitmap(pyxel_module=pyxel)
        self.assertTrue(first)

        later = self.viewport.last_refresh_at + timedelta(milliseconds=50)
        allowed = self.viewport.update_pixels([[1, 1], [1, 1]], received_at=later)
        self.assertFalse(allowed)

        after_window = self.viewport.last_refresh_at + timedelta(milliseconds=250)
        allowed = self.viewport.update_pixels([[2, 2], [2, 2]], received_at=after_window)
        self.assertTrue(allowed)
        self.assertEqual(list(self.viewport.pixel_buffer), [2, 2, 2, 2])

    def test_pixel_dimensions_guard(self):
        viewport = PyxelViewport(
            slug="invalid",
            name="Invalid",
            skin="virtual",
            pyxel_script="def draw():\n    pyxel.pset(0, 0, 1)\n",
        )
        with self.assertRaises(ValueError):
            viewport.render_bitmap(pyxel_module=DummyPyxel())

    def test_missing_draw_function(self):
        viewport = PyxelViewport(
            slug="missing-draw",
            name="Invalid",
            skin="virtual",
            columns=1,
            rows=1,
            pyxel_script="def update():\n    pass\n",
        )
        with self.assertRaises(ValueError):
            viewport.render_bitmap(pyxel_module=DummyPyxel())


class PixelScreenModelTest(TestCase):
    def test_inherits_pixel_buffer_configuration(self):
        screen = PixelScreen.objects.create(
            slug="pixel-config",
            name="Pixel Config",
            skin="virtual",
            columns=4,
            rows=4,
            pixel_format="RGBA",
            bytes_per_pixel=4,
            row_stride=16,
        )

        self.assertEqual(screen.pixel_format, "RGBA")
        self.assertEqual(screen.bytes_per_pixel, 4)
        self.assertEqual(screen.row_stride, 16)

    def test_import_guard(self):
        viewport = PyxelViewport(
            slug="guard",
            name="Guard",
            skin="virtual",
            columns=1,
            rows=1,
            pyxel_script="def draw():\n    pass\n",
        )

        with self.assertRaises(PyxelUnavailableError):
            viewport.render_bitmap(pyxel_module=None)
