from pathlib import Path
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.test import TestCase

if not django_apps.is_installed("apps.screens"):
    pytest.skip("apps.screens is not installed", allow_module_level=True)

from apps.screens.animations import (
    AnimationLoadError,
    load_frames_from_animation,
    load_frames_from_file,
)
from apps.screens.models import LCDAnimation


class LCDAnimationLoadingTests(TestCase):
    def test_packaged_animation_file_loads(self):
        animation = LCDAnimation(
            slug="trees", name="Trees", source_path="scrolling_trees.txt"
        )
        frames = list(load_frames_from_animation(animation))
        self.assertGreater(len(frames), 0)
        self.assertTrue(all(len(frame) == 32 for frame in frames))

    def test_loader_rejects_arbitrary_python_module_reference(self):
        with self.assertRaisesMessage(AnimationLoadError, "packaged"):
            load_frames_from_file("os:path")

    def test_loader_rejects_invalid_frame_length(self):
        invalid_path = Path("apps/screens/animations/invalid_test_animation.txt")
        with (
            patch(
                "apps.screens.animations.get_approved_animation_source",
                return_value=invalid_path,
            ),
            patch.object(
                Path,
                "read_text",
                return_value="too short\n",
            ),
        ):
            with self.assertRaisesMessage(AnimationLoadError, "32 characters"):
                load_frames_from_file(invalid_path.name)
