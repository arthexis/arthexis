from django.test import TestCase
from unittest.mock import patch

from .services import post_to_page


class MetaServiceTests(TestCase):
    def test_post_to_page(self):
        with patch("social.meta.services.requests.post") as mock_post:
            mock_resp = mock_post.return_value
            mock_resp.json.return_value = {"id": "1"}
            mock_resp.raise_for_status.return_value = None

            result = post_to_page("123", "hello", "token")

            mock_post.assert_called_once_with(
                "https://graph.facebook.com/123/feed",
                data={"message": "hello", "access_token": "token"},
            )
            self.assertEqual(result, {"id": "1"})
