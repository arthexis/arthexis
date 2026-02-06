from django.test import TestCase

from apps.counters.dashboard_rules import evaluate_user_story_assignment_rules
from apps.sites.models import UserStory


class UserStoryDashboardRuleTests(TestCase):
    def test_spam_user_story_does_not_break_assignment_rule(self):
        UserStory.objects.create(
            path="/",
            rating=3,
            comments="Spam feedback",
            status=UserStory.Status.SPAM,
        )

        result = evaluate_user_story_assignment_rules()

        self.assertTrue(result["success"])
