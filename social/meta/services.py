"""Utilities for interacting with Facebook via the Graph API."""

import requests

FACEBOOK_GRAPH_API = "https://graph.facebook.com"


def post_to_page(page_id: str, message: str, access_token: str) -> dict:
    """Post ``message`` to the Facebook page ``page_id``.

    Returns the JSON response from the Graph API.
    """

    url = f"{FACEBOOK_GRAPH_API}/{page_id}/feed"
    response = requests.post(url, data={"message": message, "access_token": access_token})
    response.raise_for_status()
    return response.json()
