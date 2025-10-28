from unittest import mock

import requests

from django.test import TestCase

from core.models import GoogleCalendarProfile, SecurityGroup, User


class GoogleCalendarProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="calendar-owner", password="pass12345"
        )

    def test_build_calendar_url_uses_resolved_values(self):
        profile = GoogleCalendarProfile.objects.create(
            user=self.user,
            calendar_id="example@group.calendar.google.com",
            api_key="secret",
            timezone="America/New_York",
        )

        url = profile.build_calendar_url()

        self.assertIn("example%40group.calendar.google.com", url)
        self.assertIn("America%2FNew_York", url)

    @mock.patch("core.models.requests.get")
    def test_fetch_events_parses_api_response(self, mock_get):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "items": [
                {
                    "summary": "Project Sync",
                    "htmlLink": "https://calendar.google.com/event?sync",
                    "start": {"dateTime": "2024-06-01T15:00:00+00:00"},
                    "end": {"dateTime": "2024-06-01T16:00:00+00:00"},
                    "location": "HQ",
                },
                {
                    "summary": "Holiday",
                    "start": {"date": "2024-06-02"},
                    "location": "Remote",
                },
            ]
        }
        mock_get.return_value = response

        profile = GoogleCalendarProfile.objects.create(
            user=self.user,
            calendar_id="example@group.calendar.google.com",
            api_key="secret",
            max_events=7,
        )

        events = profile.fetch_events()

        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual(first["summary"], "Project Sync")
        self.assertEqual(first["html_link"], "https://calendar.google.com/event?sync")
        self.assertFalse(first["all_day"])
        self.assertIsNotNone(first["start"])
        second = events[1]
        self.assertTrue(second["all_day"])
        self.assertEqual(second["location"], "Remote")
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["maxResults"], 7)

    @mock.patch("core.models.requests.get")
    def test_fetch_events_handles_errors(self, mock_get):
        mock_get.side_effect = requests.RequestException("boom")

        profile = GoogleCalendarProfile.objects.create(
            user=self.user,
            calendar_id="example@group.calendar.google.com",
            api_key="secret",
        )

        events = profile.fetch_events()

        self.assertEqual(events, [])

    @mock.patch("core.models.requests.get")
    def test_group_owned_profile_available_to_members(self, mock_get):
        mock_get.return_value = mock.Mock(
            raise_for_status=mock.Mock(),
            json=mock.Mock(return_value={"items": []}),
        )
        group = SecurityGroup.objects.create(name="Calendar Team")
        member = User.objects.create_user(username="member", password="pass12345")
        member.groups.add(group)

        profile = GoogleCalendarProfile.objects.create(
            group=group,
            calendar_id="group@calendar.google.com",
            api_key="secret",
        )

        resolved = member.get_profile(GoogleCalendarProfile)

        self.assertEqual(resolved, profile)

