from django.test import SimpleTestCase
from unittest import mock

from core import changelog


class ChangelogBuilderTests(SimpleTestCase):
    def test_sections_grouped_by_release(self):
        commits = [
            changelog.Commit(
                sha="a" * 40,
                date="2025-10-05",
                subject="Add integration tests for login flow (#501)",
            ),
            changelog.Commit(sha="b" * 40, date="2025-10-04", subject="Release v1.2.0"),
            changelog.Commit(
                sha="c" * 40,
                date="2025-10-03",
                subject="Improve changelog rendering behaviour (#500)",
            ),
            changelog.Commit(sha="d" * 40, date="2025-10-02", subject="Release v1.1.0"),
            changelog.Commit(
                sha="e" * 40,
                date="2025-10-01",
                subject="Refine websocket reconnection strategy (#499)",
            ),
        ]

        with mock.patch("core.changelog._read_commits", return_value=commits):
            sections = changelog.collect_sections(range_spec="HEAD")

        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0].title, "Unreleased")
        self.assertEqual(
            sections[0].entries,
            ["- " + "a" * 8 + " Add integration tests for login flow (#501)"],
        )
        self.assertEqual(sections[1].title, "v1.2.0 (2025-10-04)")
        self.assertEqual(
            sections[1].entries,
            ["- " + "c" * 8 + " Improve changelog rendering behaviour (#500)"],
        )
        self.assertEqual(sections[1].version, "1.2.0")
        self.assertEqual(sections[2].title, "v1.1.0 (2025-10-02)")
        self.assertEqual(
            sections[2].entries,
            ["- " + "e" * 8 + " Refine websocket reconnection strategy (#499)"],
        )
        self.assertEqual(sections[2].version, "1.1.0")

    def test_extract_release_notes_falls_back_to_unreleased(self):
        release_title = "v1.1.0 (2025-10-02)"
        content = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "Unreleased",
                "----------",
                "",
                "- pending change",
                "",
                release_title,
                "-" * len(release_title),
                "",
                "- shipped change",
                "",
            ]
        )

        self.assertEqual(
            changelog.extract_release_notes(content, "1.1.0"), "- shipped change"
        )
        self.assertEqual(
            changelog.extract_release_notes(content, "2.0.0"), "- pending change"
        )
