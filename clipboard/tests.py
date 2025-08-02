from django.test import TestCase

from .models import Pattern


class PatternMatchTests(TestCase):
    def test_match_with_sigil(self):
        pattern = Pattern.objects.create(mask="This is [not] good", priority=1)
        substitutions = pattern.match("Indeed, this is very good.")
        self.assertEqual(substitutions, {"not": "very"})

    def test_match_without_sigil(self):
        pattern = Pattern.objects.create(mask="simple", priority=1)
        substitutions = pattern.match("a simple example")
        self.assertEqual(substitutions, {})

    def test_no_match(self):
        pattern = Pattern.objects.create(mask="missing", priority=1)
        self.assertIsNone(pattern.match("nothing to see"))
