from django.test import TestCase


class FooterPresenceTests(TestCase):
    def test_home_page_includes_footer(self):
        response = self.client.get("/")
        self.assertContains(response, "<footer", html=False)
        self.assertContains(response, "arthexis", html=False)
