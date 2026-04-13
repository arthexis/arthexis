from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

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
        private = create_gallery_image(uploaded_file=self._upload("private.jpg"), title="Private")
        public = create_gallery_image(
            uploaded_file=self._upload("public.jpg"),
            title="Public",
            include_in_public_gallery=True,
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
            {"image": self._upload(), "title": "Managed", "description": "", "include_in_public_gallery": True},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(GalleryImage.objects.filter(title="Managed").exists())

    def test_trait_assignment_allows_category_trait_pair(self):
        image = create_gallery_image(uploaded_file=self._upload("meta.jpg"), title="Meta")
        category = GalleryCategory.objects.create(name="Style", slug="style")
        trait = GalleryTrait.objects.create(name="Tone", slug="tone")
        assignment = GalleryImageTrait.objects.create(
            image=image,
            category=category,
            trait=trait,
            qualitative_value="warm",
        )
        self.assertEqual(assignment.float_value, 1.0)
