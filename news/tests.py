from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from core.models import NewsArticle


class NewsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        NewsArticle.objects.create(
            name="0.1.7 Latest release",
            content="Details",
            published=date(2025, 3, 5),
        )
        NewsArticle.objects.create(
            name="0.1.4 Mid release",
            content="Details",
            published=date(2024, 7, 10),
        )
        NewsArticle.objects.create(
            name="0.1.1 First release",
            content="Details",
            published=date(2024, 1, 15),
        )

    def test_latest_article_first(self):
        resp = self.client.get(reverse("news:list"))
        articles = list(resp.context["object_list"])
        self.assertEqual(articles[0].name, "0.1.7 Latest release")

    def test_sidebar_version_order(self):
        resp = self.client.get(reverse("news:list"))
        titles = [a.name for a in resp.context["all_articles"]]
        self.assertEqual(
            titles,
            [
                "0.1.7 Latest release",
                "0.1.4 Mid release",
                "0.1.1 First release",
            ],
        )
