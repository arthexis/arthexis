from django.test import SimpleTestCase
from unittest import mock
import subprocess

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

    def test_duplicate_release_commits_are_merged(self):
        commits = [
            changelog.Commit(sha="a" * 40, date="2025-10-06", subject="Release v1.3.0"),
            changelog.Commit(
                sha="b" * 40,
                date="2025-10-05",
                subject="Handle changelog duplicate merges (#601)",
            ),
            changelog.Commit(sha="c" * 40, date="2025-10-05", subject="Release v1.3.0"),
            changelog.Commit(
                sha="d" * 40,
                date="2025-10-04",
                subject="Improve release retry messaging (#600)",
            ),
        ]

        with mock.patch("core.changelog._read_commits", return_value=commits):
            sections = changelog.collect_sections(range_spec="HEAD")

        self.assertEqual(len(sections), 2)
        release = sections[1]
        self.assertEqual(release.title, "v1.3.0 (2025-10-06)")
        self.assertEqual(release.version, "1.3.0")
        self.assertEqual(
            release.entries,
            [
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "- " + "d" * 8 + " Improve release retry messaging (#600)",
            ],
        )

    def test_previous_sections_merge_without_duplicates(self):
        commits = [
            changelog.Commit(sha="a" * 40, date="2025-10-06", subject="Release v1.3.0"),
            changelog.Commit(
                sha="b" * 40,
                date="2025-10-05",
                subject="Handle changelog duplicate merges (#601)",
            ),
        ]

        previous_text = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "v1.3.0 (2025-10-06)",
                "-------------------",
                "",
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "- " + "c" * 8 + " Backfill missing release notes (#599)",
                "",
            ]
        )

        with mock.patch("core.changelog._read_commits", return_value=commits):
            sections = changelog.collect_sections(
                range_spec="HEAD", previous_text=previous_text
            )

        release = sections[1]
        self.assertEqual(
            release.entries,
            [
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "- " + "c" * 8 + " Backfill missing release notes (#599)",
            ],
        )

    def test_determine_range_spec_uses_previous_tag_for_exact_match(self):
        def fake_run(cmd, capture_output=False, text=False, check=False):
            if cmd == ["git", "describe", "--tags", "--exact-match", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="v0.1.14\n", stderr="")
            if cmd == ["git", "rev-parse", "--verify", "HEAD^"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd == ["git", "describe", "--tags", "--abbrev=0", "HEAD^"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="v0.1.13\n", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        with mock.patch("core.changelog.subprocess.run", side_effect=fake_run):
            self.assertEqual(changelog.determine_range_spec(), "v0.1.13..HEAD")

    def test_determine_range_spec_without_previous_tag(self):
        def fake_run(cmd, capture_output=False, text=False, check=False):
            if cmd == ["git", "describe", "--tags", "--exact-match", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="v0.1.0\n", stderr="")
            if cmd == ["git", "rev-parse", "--verify", "HEAD^"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        with mock.patch("core.changelog.subprocess.run", side_effect=fake_run):
            self.assertEqual(changelog.determine_range_spec(), "HEAD")

    def test_determine_range_spec_without_exact_match(self):
        def fake_run(cmd, capture_output=False, text=False, check=False):
            if cmd == ["git", "describe", "--tags", "--exact-match", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            if cmd == ["git", "describe", "--tags", "--abbrev=0"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="v0.1.13\n", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        with mock.patch("core.changelog.subprocess.run", side_effect=fake_run):
            self.assertEqual(changelog.determine_range_spec(), "v0.1.13..HEAD")
