from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AdminModelGraphTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="diagram-admin",
            email="diagram@example.com",
            password="password",
        )
        self.client.force_login(self.user)

    def test_admin_index_contains_graph_links(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, 'data-app-graph="teams"')
        self.assertContains(
            response, reverse("admin-model-graph", args=["teams"])
        )

    def test_model_graph_view_renders_context(self):
        url = reverse("admin-model-graph", args=["teams"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        graph_source = response.context["graph_source"]
        self.assertIn("digraph", graph_source)
        self.assertIn("PowerLead", graph_source)
        self.assertContains(response, "viz-standalone.js")
        self.assertContains(response, "Included models")

    def test_invalid_app_returns_404(self):
        response = self.client.get(reverse("admin-model-graph", args=["invalid"]))
        self.assertEqual(response.status_code, 404)
