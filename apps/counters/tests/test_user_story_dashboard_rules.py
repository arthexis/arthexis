from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from apps.counters.dashboard_rules import evaluate_user_story_assignment_rules
from apps.counters.models import DashboardRule
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

    def test_dashboard_rule_cache_invalidates_on_soft_delete(self):
        story = UserStory.objects.create(
            path="/",
            rating=3,
            comments="Test feedback",
            is_seed_data=True,
        )
        content_type = ContentType.objects.get_for_model(
            UserStory, for_concrete_model=False
        )
        rule = DashboardRule.objects.get(content_type=content_type)

        cached = DashboardRule.get_cached_value(content_type, rule.evaluate)
        self.assertFalse(cached["success"])

        story.is_deleted = True
        story.save(update_fields=["is_deleted"])

        refreshed = DashboardRule.get_cached_value(content_type, rule.evaluate)
        self.assertTrue(refreshed["success"])
