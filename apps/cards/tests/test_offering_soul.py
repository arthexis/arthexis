from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.cards.forms import OfferingSoulUploadForm
from apps.cards.models import OfferingSoul
from apps.cards.soul import derive_soul_package


class OfferingSoulDerivationTests(TestCase):
    def test_derivation_is_deterministic_for_same_file(self):
        payload = (b"A" * 256) + (b"\x00\x01\x02" * 200) + b"tail"

        package_one = derive_soul_package(SimpleUploadedFile("sample.bin", payload))
        package_two = derive_soul_package(SimpleUploadedFile("sample.bin", payload))

        self.assertEqual(package_one, package_two)
        self.assertEqual(package_one["core_hash"], package_two["core_hash"])
        self.assertEqual(package_one["traits"]["structural"], package_two["traits"]["structural"])

    def test_image_derivation_exposes_type_traits_without_storing_file(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\xf8\xcf\x00\x00"
            b"\x03\x01\x01\x00\x18\xdd\x8e\xbd\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        form = OfferingSoulUploadForm(
            data={"issuance_marker": "v1"},
            files={"offering_file": SimpleUploadedFile("pixel.png", png, content_type="image/png")},
        )

        self.assertTrue(form.is_valid(), form.errors)
        soul = form.save()

        self.assertIsInstance(soul, OfferingSoul)
        self.assertEqual(soul.file_size_bytes, len(png))
        self.assertIn("image", soul.type_traits)
        self.assertNotIn("raw", soul.package)

    def test_form_rejects_file_over_25mb(self):
        oversized = SimpleUploadedFile("too-big.bin", b"x" * (25 * 1024 * 1024 + 1))
        form = OfferingSoulUploadForm(data={}, files={"offering_file": oversized})

        self.assertFalse(form.is_valid())
        self.assertIn("offering_file", form.errors)
