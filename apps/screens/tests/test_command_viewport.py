from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.screens.models import DeviceScreen, PyxelUnavailableError, PyxelViewport


class ViewportCommandTests(TestCase):
    def test_command_runs_viewport_by_slug(self):
        viewport = PyxelViewport.objects.create(
            slug="cmd-viewport",
            name="Command Viewport",
            category=DeviceScreen.Category.PIXEL,
            skin="virtual",
            columns=8,
            rows=8,
            pyxel_script="""def draw():\n    pyxel.pset(0, 0, 1)\n""",
        )

        with patch.object(PyxelViewport, "open_viewport") as run_viewport:
            call_command("viewport", viewport.slug)
        run_viewport.assert_called_once()

    def test_command_handles_missing_pyxel(self):
        viewport = PyxelViewport.objects.create(
            slug="cmd-viewport",
            name="Command Viewport",
            category=DeviceScreen.Category.PIXEL,
            skin="virtual",
            columns=8,
            rows=8,
            pyxel_script="""def draw():\n    pyxel.pset(0, 0, 1)\n""",
        )

        with patch.object(
            PyxelViewport, "open_viewport", side_effect=PyxelUnavailableError("Pyxel library is required")
        ), self.assertRaisesMessage(CommandError, "Pyxel library is required"):
            call_command("viewport", viewport.slug)
