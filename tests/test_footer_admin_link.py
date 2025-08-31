from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.models import Package, PackageRelease


class FooterAdminLinkTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.package, _ = Package.objects.get_or_create(name="arthexis")
        self.release, _ = PackageRelease.objects.get_or_create(
            version="0.1.1", defaults={"package": self.package}
        )
        User = get_user_model()
        self.staff = User.objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        self.user = User.objects.create_user(username="user", password="pw")

    def _render(self, user):
        request = self.factory.get("/")
        request.user = user
        tmpl = Template("{% load ref_tags %}{% render_footer %}")
        return tmpl.render(Context({"request": request}))

    def test_staff_sees_link_to_release_admin(self):
        html = self._render(self.staff)
        url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        self.assertIn(f'href="{url}"', html)

    def test_non_staff_does_not_see_link(self):
        html = self._render(self.user)
        url = reverse("admin:core_packagerelease_change", args=[self.release.pk])
        self.assertNotIn(f'href="{url}"', html)
