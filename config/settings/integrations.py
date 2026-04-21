"""Third-party integration settings."""

import os

from utils.env import env_bool

EVERGO_API_LOGIN_URL = os.environ.get(
    "EVERGO_API_LOGIN_URL", "https://portal-backend.evergo.com/api/mex/v1/login"
)
EVERGO_PORTAL_LOGIN_URL = os.environ.get(
    "EVERGO_PORTAL_LOGIN_URL", "https://portal-mex.evergo.com/access/login"
)

# Slack bot onboarding
SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_SCOPES = os.environ.get(
    "SLACK_BOT_SCOPES",
    "commands,chat:write,chat:write.public",
)
SLACK_REDIRECT_URL = os.environ.get("SLACK_REDIRECT_URL", "")

# GitHub issue reporting
GITHUB_ISSUE_REPORTING_ENABLED = env_bool("GITHUB_ISSUE_REPORTING_ENABLED", True)
GITHUB_ISSUE_REPORTING_COOLDOWN = 3600  # seconds

# GitHub operator OAuth login
GITHUB_OAUTH_CLIENT_ID = os.environ.get("GITHUB_OAUTH_CLIENT_ID", "")
GITHUB_OAUTH_CLIENT_SECRET = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "")
GITHUB_OAUTH_SCOPES = os.environ.get("GITHUB_OAUTH_SCOPES", "repo read:user")
