from unittest import mock

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

    def test_admin_index_omits_graph_links(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-app-graph="teams"')
        self.assertNotContains(response, reverse("admin-model-graph", args=["teams"]))

    def test_admin_docs_index_links_to_model_graphs(self):
        response = self.client.get(reverse("django-admindocs-docroot"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("django-admindocs-model-graphs"))
        self.assertContains(response, "Model Graphs")

    def test_model_graph_docs_lists_sections(self):
        response = self.client.get(reverse("django-admindocs-model-graphs"))
        self.assertEqual(response.status_code, 200)

        sections = response.context["sections"]
        self.assertGreaterEqual(len(sections), 3)
        names = [section["verbose_name"] for section in sections]
        self.assertEqual(names[:3], ["1. Power", "2. Business", "3. Protocol"])

        teams_graph_url = reverse("admin-model-graph", args=["teams"])
        self.assertContains(response, teams_graph_url)

        model_links = sections[0]["models"]
        self.assertTrue(model_links)
        self.assertIn("doc_url", model_links[0])

    def test_model_graph_view_renders_context(self):
        url = reverse("admin-model-graph", args=["teams"])
        with (
            mock.patch("pages.views.shutil.which", return_value="/usr/bin/dot"),
            mock.patch(
                "graphviz.graphs.Digraph.pipe",
                return_value="<svg class='mock-diagram'></svg>",
            ),
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        graph_source = response.context["graph_source"]
        self.assertIn("digraph", graph_source)
        self.assertIn("PowerLead", graph_source)
        self.assertIn("<svg", response.context["graph_svg"])
        self.assertEqual(response.context["graph_error"], "")
        self.assertContains(response, "Included models")
        self.assertContains(response, 'role="img"')

    def test_model_graph_view_handles_missing_graphviz(self):
        url = reverse("admin-model-graph", args=["teams"])
        with mock.patch("pages.views.shutil.which", return_value=None):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["graph_svg"], "")
        self.assertIn("Graphviz executables", response.context["graph_error"])
        self.assertContains(
            response,
            "Graphviz executables are required to render this diagram.",
        )

    def test_invalid_app_returns_404(self):
        response = self.client.get(reverse("admin-model-graph", args=["invalid"]))
        self.assertEqual(response.status_code, 404)
