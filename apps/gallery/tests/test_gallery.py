from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from apps.content.models import ContentSample
from apps.groups.models import SecurityGroup
from apps.media.models import MediaFile
from apps.shop.models import Shop, ShopProduct

from ..constants import GALLERY_MANAGER_GROUP_NAME
from ..models import (
    GalleryCategory,
    GalleryCredit,
    GalleryImage,
    GalleryImageTrait,
    GalleryTrait,
)
from ..services import create_gallery_image
from ..views import _apply_gallery_search


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
                method="GAL_UPLOAD",
            ).exists()
        )

    def test_content_sample_failure_cleans_up_media_file(self):
        before_count = MediaFile.objects.count()
        with mock.patch("apps.gallery.services._save_gallery_content_sample", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                create_gallery_image(
                    uploaded_file=self._upload("cleanup.jpg"),
                    title="Cleanup",
                    create_content_sample=True,
                    owner_user=self.user,
                )
        self.assertEqual(MediaFile.objects.count(), before_count)
        self.assertFalse(GalleryImage.objects.filter(title="Cleanup").exists())


class GalleryIndexTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="gallery-search-owner", password="pw")

    def _upload(self, name="gallery.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), "yellow").save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def test_index_search_matches_image_fields_and_related_traits(self):
        visible = create_gallery_image(
            uploaded_file=self._upload("searchable.jpg"),
            title="Sunlit Plaza",
            description="Warm stone plaza",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        hidden = create_gallery_image(
            uploaded_file=self._upload("hidden.jpg"),
            title="Forest",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        category = GalleryCategory.objects.create(name="Palette", slug="palette")
        trait = GalleryTrait.objects.create(name="Mood", slug="mood")
        GalleryImageTrait.objects.create(
            image=visible,
            category=category,
            trait=trait,
            qualitative_value="citrine",
        )

        response = self.client.get("/gallery/", {"q": "citrine"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sunlit Plaza")
        self.assertNotContains(response, "Forest")
        self.assertEqual(list(response.context["images"]), [visible])
        self.assertNotIn(hidden, list(response.context["images"]))

    def test_index_search_combines_direct_and_multivalued_matches_by_pk(self):
        direct_match = create_gallery_image(
            uploaded_file=self._upload("direct.jpg"),
            title="Citrine Skyline",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        related_match = create_gallery_image(
            uploaded_file=self._upload("related.jpg"),
            title="Related Match",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        hidden = create_gallery_image(
            uploaded_file=self._upload("hidden.jpg"),
            title="Forest",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        category = GalleryCategory.objects.create(name="Palette", slug="palette")
        trait = GalleryTrait.objects.create(name="Mood", slug="mood")
        GalleryImageTrait.objects.create(
            image=related_match,
            category=category,
            trait=trait,
            qualitative_value="citrine",
        )
        GalleryCredit.objects.create(
            image=related_match,
            display_name="Citrine Archive",
        )

        matches = list(_apply_gallery_search(GalleryImage.objects.all(), "citrine").order_by("title"))

        self.assertEqual(matches, [direct_match, related_match])
        self.assertNotIn(hidden, matches)

    def test_index_search_keeps_multivalued_joins_inside_pk_subqueries(self):
        queryset = _apply_gallery_search(GalleryImage.objects.all(), "citrine")

        sql = str(queryset.query)
        outer_select = sql.split(" WHERE ", 1)[0]

        self.assertIn(" IN (SELECT ", sql)
        self.assertNotIn("apps_gallery_galleryimage_categories", outer_select)
        self.assertNotIn("apps_gallery_gallerycredit", outer_select)
        self.assertNotIn("apps_gallery_galleryimagetrait", outer_select)

    def test_index_search_matches_exact_ids_without_partial_numeric_matches(self):
        visible = create_gallery_image(
            uploaded_file=self._upload("id-match.jpg"),
            title="Numeric Match",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        other = create_gallery_image(
            uploaded_file=self._upload("id-other.jpg"),
            title="Numeric Other",
            owner_user=self.user,
            include_in_public_gallery=True,
        )

        matches = list(_apply_gallery_search(GalleryImage.objects.all(), str(visible.id)))

        self.assertIn(visible, matches)
        self.assertNotIn(other, matches)

    def test_index_search_matches_exact_gallery_slug(self):
        visible = create_gallery_image(
            uploaded_file=self._upload("slug-match.jpg"),
            title="Slug Match",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        other = create_gallery_image(
            uploaded_file=self._upload("slug-other.jpg"),
            title="Slug Other",
            owner_user=self.user,
            include_in_public_gallery=True,
        )

        matches = list(_apply_gallery_search(GalleryImage.objects.all(), str(visible.slug)))

        self.assertEqual(matches, [visible])
        self.assertNotIn(other, matches)

    def test_detail_layout_includes_navigation_feedback_context_and_store_link(self):
        first = create_gallery_image(
            uploaded_file=self._upload("first.jpg"),
            title="Alpha",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        current = create_gallery_image(
            uploaded_file=self._upload("current.jpg"),
            title="Beacon",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        same_title_next = create_gallery_image(
            uploaded_file=self._upload("same-title-next.jpg"),
            title="Beacon",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        last = create_gallery_image(
            uploaded_file=self._upload("last.jpg"),
            title="Coda",
            owner_user=self.user,
            include_in_public_gallery=True,
        )
        shop = Shop.objects.create(name="RF Store", slug="rf-store")
        ShopProduct.objects.create(
            shop=shop,
            name="RF Card",
            sku="RF-1",
            unit_price="10.00",
            stock_quantity=5,
            supports_gallery_image_printing=True,
        )

        response = self.client.get(f"/gallery/images/{current.slug}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Image ID")
        self.assertContains(response, f"data-feedback-context=\"Image ID: {current.id}")
        self.assertContains(response, f"/gallery/images/{first.slug}/")
        self.assertContains(response, f"/gallery/images/{same_title_next.slug}/")
        self.assertNotContains(response, f"/gallery/images/{last.slug}/")
        self.assertContains(response, "Use for RF Card")
        self.assertContains(response, f"/shop/?gallery_image={current.id}")


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

    def test_upload_form_defaults_owner_group_to_gallery_manager(self):
        self.client.force_login(self.manager)
        response = self.client.get("/gallery/upload/")
        self.assertContains(response, f'<option value="{self.group.pk}" selected>')

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

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=0)
    def test_upload_reuses_staged_file_after_validation_error(self):
        self.client.force_login(self.manager)
        initial_response = self.client.post(
            "/gallery/upload/",
            {
                "image": self._upload("retry.jpg"),
                "title": "",
                "description": "",
                "owner_user": self.manager.username,
            },
        )
        self.assertEqual(initial_response.status_code, 200)
        self.assertContains(initial_response, "Image file is retained from your previous attempt.")
        staged_upload_key = initial_response.context["form"]["staged_upload_key"].value()
        self.assertTrue(staged_upload_key)

        retry_response = self.client.post(
            "/gallery/upload/",
            {
                "title": "Retry Works",
                "description": "",
                "owner_user": self.manager.username,
                "staged_upload_key": staged_upload_key,
            },
        )
        self.assertEqual(retry_response.status_code, 302)
        self.assertTrue(GalleryImage.objects.filter(title="Retry Works").exists())

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=0)
    def test_upload_with_invalid_image_does_not_stage_file(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            "/gallery/upload/",
            {
                "image": SimpleUploadedFile("not-image.txt", b"not-an-image", content_type="text/plain"),
                "title": "Invalid Image",
                "description": "",
                "owner_user": self.manager.username,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a valid image.")
        self.assertFalse(response.context["form"]["staged_upload_key"].value())
        self.assertFalse(GalleryImage.objects.filter(title="Invalid Image").exists())

    def test_expired_or_invalid_staged_key_returns_form_error(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            "/gallery/upload/",
            {
                "title": "Retry Fails",
                "description": "",
                "owner_user": self.manager.username,
                "staged_upload_key": signing.TimestampSigner(salt="gallery-upload").sign("gallery/staged/999/missing.jpg"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The previously uploaded image has expired or is invalid. Please upload it again.")
        self.assertFalse(GalleryImage.objects.filter(title="Retry Fails").exists())

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


class GalleryCategoryDefaultsTests(TestCase):
    def test_default_gallery_categories_are_seeded(self):
        self.assertQuerySetEqual(
            GalleryCategory.objects.filter(slug__in=("artist", "designer", "developer", "template")).order_by("slug"),
            ["artist", "designer", "developer", "template"],
            transform=lambda category: category.slug,
        )


class GalleryImageSharingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner-share", password="pw")
        self.recipient = get_user_model().objects.create_user(username="recipient-share", password="pw")
        self.other = get_user_model().objects.create_user(username="other-share", password="pw")
        self.image = create_gallery_image(
            uploaded_file=self._upload("shared.jpg"),
            title="Shared",
            owner_user=self.owner,
        )

    def _upload(self, name="share.jpg"):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), "purple").save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def test_owner_can_share_without_relinquishing_ownership(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            f"/gallery/images/{self.image.slug}/",
            {"action": "share-image", "username": self.recipient.username},
        )
        self.assertEqual(response.status_code, 302)
        self.image.refresh_from_db()
        self.assertEqual(self.image.owner_user_id, self.owner.pk)
        self.assertTrue(self.image.shared_with_users.filter(pk=self.recipient.pk).exists())
        self.assertTrue(self.image.can_view(self.recipient))

    def test_shared_user_cannot_reshare_image(self):
        self.image.shared_with_users.add(self.recipient)
        self.client.force_login(self.recipient)
        response = self.client.post(
            f"/gallery/images/{self.image.slug}/",
            {"action": "share-image", "username": self.other.username},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.image.shared_with_users.filter(pk=self.other.pk).exists())
