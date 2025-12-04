from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.screens.models import DeviceScreen, PyxelViewport


class DeviceScreenTests(TestCase):
    def test_update_bitmap_respects_refresh_rate(self):
        screen = DeviceScreen.objects.create(
            slug="unit",
            name="Unit Test Screen",
            category=DeviceScreen.Category.PIXEL,
            skin="virtual",
            columns=2,
            rows=2,
            min_refresh_ms=100,
        )

        first_time = timezone.now()
        first = screen.update_bitmap([[1, 0], [0, 1]], received_at=first_time)
        self.assertTrue(first)
        self.assertEqual(screen.last_bitmap, [[1, 0], [0, 1]])

        too_soon = first_time + timedelta(milliseconds=50)
        second = screen.update_bitmap([[2, 2], [2, 2]], received_at=too_soon)
        self.assertFalse(second)
        self.assertEqual(screen.last_bitmap, [[1, 0], [0, 1]])

        later = first_time + timedelta(milliseconds=150)
        third = screen.update_bitmap([[3, 3], [3, 3]], received_at=later)
        self.assertTrue(third)
        self.assertEqual(screen.last_bitmap, [[3, 3], [3, 3]])


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
            category=DeviceScreen.Category.PIXEL,
            skin="virtual",
            columns=2,
            rows=2,
            pyxel_script=PYXEL_SCRIPT,
            pyxel_fps=30,
        )

    def test_render_bitmap_runs_script(self):
        pyxel = DummyPyxel()
        bitmap = self.viewport.render_bitmap(pyxel_module=pyxel)
        self.assertEqual(bitmap, [[5, 0], [0, 9]])
        self.assertEqual(self.viewport.last_bitmap, [[5, 0], [0, 9]])
        self.assertTrue(pyxel.quit_called)

    def test_open_viewport_uses_run_loop(self):
        pyxel = DummyPyxel()
        self.viewport.open_viewport(pyxel_module=pyxel)
        self.assertTrue(pyxel.run_called)
        self.assertEqual(self.viewport.last_bitmap, [[5, 0], [0, 9]])
