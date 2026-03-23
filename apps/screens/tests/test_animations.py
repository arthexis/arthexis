from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.screens.animations import AnimationLoadError, load_frames_from_animation, load_frames_from_file
from apps.screens.models import LCDAnimation


class LCDAnimationLoadingTests(TestCase):
    def test_packaged_animation_file_loads(self):
        animation = LCDAnimation(slug="trees", name="Trees", source_path="scrolling_trees.txt")

        frames = list(load_frames_from_animation(animation))

        self.assertGreater(len(frames), 0)
        self.assertTrue(all(len(frame) == 32 for frame in frames))

    def test_model_rejects_unapproved_animation_source(self):
        animation = LCDAnimation(slug="bad-source", name="Bad Source", source_path="../secrets.txt")

        with self.assertRaises(ValidationError):
            animation.full_clean()

    def test_loader_rejects_arbitrary_python_module_reference(self):
        with self.assertRaisesMessage(AnimationLoadError, "packaged"):
            load_frames_from_file("os:path")

    def test_loader_rejects_non_packaged_file(self):
        with self.assertRaisesMessage(AnimationLoadError, "packaged"):
            load_frames_from_file("missing.txt")

    def test_loader_rejects_invalid_frame_length(self):
        fixtures_dir = Path("apps/screens/animations")
        invalid_path = fixtures_dir / "invalid_test_animation.txt"
        invalid_path.write_text("too short\n", encoding="utf-8")
        self.addCleanup(invalid_path.unlink)

        with self.assertRaisesMessage(AnimationLoadError, "32 characters"):
            load_frames_from_file(invalid_path.name)
