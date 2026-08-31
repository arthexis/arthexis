from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_assignment_settings_keep_prefixed_environment_fallbacks():
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env["ARTHEXIS_REPOSITORY_ASSIGNMENT_UPSTREAM_URL"] = "https://upstream.example"
    env["ARTHEXIS_REPOSITORY_ASSIGNMENT_SYNC_TOKEN"] = "prefixed-token"
    for key in (
        "REPOSITORY_ASSIGNMENT_UPSTREAM_URL",
        "REPOSITORY_WORK_ASSIGNMENT_UPSTREAM_URL",
        "REPOSITORY_ASSIGNMENT_SYNC_TOKEN",
        "REPOSITORY_WORK_ASSIGNMENT_SYNC_TOKEN",
    ):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; "
                "from django.conf import settings; "
                "print(json.dumps({"
                "'url': settings.REPOSITORY_ASSIGNMENT_UPSTREAM_URL, "
                "'work_url': settings.REPOSITORY_WORK_ASSIGNMENT_UPSTREAM_URL, "
                "'token': settings.REPOSITORY_ASSIGNMENT_SYNC_TOKEN, "
                "'work_token': settings.REPOSITORY_WORK_ASSIGNMENT_SYNC_TOKEN"
                "}, sort_keys=True))"
            ),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload == {
        "url": "https://upstream.example",
        "work_url": "https://upstream.example",
        "token": "prefixed-token",
        "work_token": "prefixed-token",
    }
