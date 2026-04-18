from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from apps.content.models import ContentSample
from apps.groups.models import SecurityGroup

from ..constants import GALLERY_MANAGER_GROUP_NAME
from ..models import GalleryCategory, GalleryImage, GalleryImageTrait, GalleryTrait
from ..services import create_gallery_image


class GalleryVisibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="owner", password="pw")
        self.other = get_user_model().objects.create_user(username="other", password="pw")

    def _upload(self, name="a.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), "blue").save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def test_public_flag_controls_visibility(self):
        private = create_gallery_image(uploaded_file=self._upload("private.jpg"), title="Private", owner_user=self.user)
        public = create_gallery_image(
            uploaded_file=self._upload("public.jpg"),
            title="Public",
            include_in_public_gallery=True,
            owner_user=self.user,
        )
        self.assertFalse(private.can_view(self.other))
        self.assertTrue(public.can_view(self.other))

    def test_owner_can_view_private_image(self):
        image = create_gallery_image(
            uploaded_file=self._upload("owner.jpg"),
            title="Owner",
            owner_user=self.user,
        )
        self.assertTrue(image.can_view(self.user))
        self.assertFalse(image.can_view(self.other))

    def test_optional_content_sample_link_is_created_when_requested(self):
        image = create_gallery_image(
            uploaded_file=self._upload("sampled.jpg"),
            title="Sampled",
            create_content_sample=True,
            owner_user=self.user,
        )
        self.assertIsNotNone(image.content_sample_id)
        self.assertTrue(
            ContentSample.objects.filter(
                pk=image.content_sample_id,
                kind=ContentSample.IMAGE,
                method="GALLERY_UPLOAD",
            ).exists()
        )


class GalleryManagementPermissionTests(TestCase):
    def setUp(self):
        self.group = SecurityGroup.objects.create(name=GALLERY_MANAGER_GROUP_NAME)
        self.manager = get_user_model().objects.create_user(username="manager", password="pw")
        self.manager.groups.add(self.group)

    def _upload(self, name="u.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), "green").save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def test_group_member_can_upload(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            "/gallery/upload/",
            {
                "image": self._upload(),
                "title": "Managed",
                "description": "",
                "include_in_public_gallery": True,
                "owner_user": self.manager.username,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(GalleryImage.objects.filter(title="Managed").exists())

    def test_non_manager_cannot_upload(self):
        non_manager = get_user_model().objects.create_user(username="non-manager", password="pw")
        self.client.force_login(non_manager)
        response = self.client.post(
            "/gallery/upload/",
            {"image": self._upload("denied.jpg"), "title": "Denied", "description": "", "include_in_public_gallery": True},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(GalleryImage.objects.filter(title="Denied").exists())

    def test_upload_view_creates_content_sample_when_checkbox_is_selected(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            "/gallery/upload/",
            {
                "image": self._upload("sampled-managed.jpg"),
                "title": "Managed With Sample",
                "description": "",
                "include_in_public_gallery": True,
                "create_content_sample": True,
                "owner_user": self.manager.username,
            },
        )
        self.assertEqual(response.status_code, 302)
        image = GalleryImage.objects.get(title="Managed With Sample")
        self.assertIsNotNone(image.content_sample_id)

    def test_gallery_manager_can_view_private_image(self):
        image = create_gallery_image(
            uploaded_file=self._upload("managed-private.jpg"),
            title="Managed Private",
            owner_user=get_user_model().objects.create_user(username="owned", password="pw"),
        )
        self.assertTrue(image.can_view(self.manager))

    def test_trait_assignment_allows_category_trait_pair(self):
        image = create_gallery_image(uploaded_file=self._upload("meta.jpg"), title="Meta", owner_user=self.manager)
        category = GalleryCategory.objects.create(name="Style", slug="style")
        trait = GalleryTrait.objects.create(name="Tone", slug="tone")
        assignment = GalleryImageTrait.objects.create(
            image=image,
            category=category,
            trait=trait,
            qualitative_value="warm",
        )
        self.assertEqual(assignment.float_value, 1.0)

    def test_duplicate_trait_submission_updates_existing_assignment(self):
        self.client.force_login(self.manager)
        image = create_gallery_image(uploaded_file=self._upload("dup.jpg"), title="Dup", owner_user=self.manager)
        category = GalleryCategory.objects.create(name="Palette", slug="palette")
        trait = GalleryTrait.objects.create(name="Mood", slug="mood")

        first_response = self.client.post(
            f"/gallery/images/{image.slug}/",
            {
                "action": "add-trait",
                "category": category.pk,
                "trait": trait.pk,
                "qualitative_value": "warm",
                "float_value": "0.5",
            },
        )
        second_response = self.client.post(
            f"/gallery/images/{image.slug}/",
            {
                "action": "add-trait",
                "category": category.pk,
                "trait": trait.pk,
                "qualitative_value": "warm",
                "float_value": "0.8",
            },
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(
            GalleryImageTrait.objects.filter(
                image=image,
                category=category,
                trait=trait,
                qualitative_value="warm",
            ).count(),
            1,
        )
        assignment = GalleryImageTrait.objects.get(
            image=image,
            category=category,
            trait=trait,
            qualitative_value="warm",
        )
        self.assertEqual(assignment.float_value, 0.8)
