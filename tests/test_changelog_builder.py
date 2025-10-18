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

    def test_release_detection_tolerates_missing_v_prefix(self):
        commits = [
            changelog.Commit(
                sha="a" * 40,
                date="2025-10-08",
                subject="Add admin polish for release flow (#610)",
            ),
            changelog.Commit(
                sha="b" * 40,
                date="2025-10-07",
                subject="pre-release commit 1.4.0",
            ),
            changelog.Commit(
                sha="c" * 40,
                date="2025-10-06",
                subject="Improve changelog robustness (#609)",
            ),
            changelog.Commit(
                sha="d" * 40,
                date="2025-10-05",
                subject="release v1.3.0",
            ),
            changelog.Commit(
                sha="e" * 40,
                date="2025-10-04",
                subject="Tighten release logging (#608)",
            ),
        ]

        with mock.patch("core.changelog._read_commits", return_value=commits):
            sections = changelog.collect_sections(range_spec="HEAD")

        self.assertEqual(len(sections), 3)
        unreleased, current, previous = sections
        self.assertEqual(
            unreleased.entries,
            ["- " + "a" * 8 + " Add admin polish for release flow (#610)"],
        )
        self.assertEqual(current.title, "v1.4.0 (2025-10-07)")
        self.assertEqual(current.version, "1.4.0")
        self.assertEqual(
            current.entries,
            ["- " + "c" * 8 + " Improve changelog robustness (#609)"],
        )
        self.assertEqual(previous.title, "v1.3.0 (2025-10-05)")
        self.assertEqual(previous.version, "1.3.0")
        self.assertEqual(
            previous.entries,
            ["- " + "e" * 8 + " Tighten release logging (#608)"],
        )

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

    def test_regeneration_without_new_commits_preserves_latest_release(self):
        previous_text = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "Unreleased",
                "----------",
                "",
                "- " + "a" * 8 + " Existing unreleased entry",
                "",
                "v1.3.0 (2025-10-06)",
                "-------------------",
                "",
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "",
            ]
        )

        with mock.patch("core.changelog._read_commits", return_value=[]):
            sections = changelog.collect_sections(
                range_spec="HEAD", previous_text=previous_text
            )

        self.assertEqual(len(sections), 2)
        unreleased, latest = sections
        self.assertEqual(unreleased.title, "Unreleased")
        self.assertEqual(unreleased.entries, [])
        self.assertEqual(latest.title, "v1.3.0 (2025-10-06)")
        self.assertEqual(
            latest.entries,
            ["- " + "b" * 8 + " Handle changelog duplicate merges (#601)"],
        )

    def test_regeneration_with_new_commits_preserves_latest_release(self):
        previous_text = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "Unreleased",
                "----------",
                "",
                "- " + "a" * 8 + " Existing unreleased entry",
                "",
                "v1.3.0 (2025-10-06)",
                "-------------------",
                "",
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "",
            ]
        )

        commits = [
            changelog.Commit(
                sha="d" * 40,
                date="2025-10-07",
                subject="Fix changelog regression (#604)",
            )
        ]

        with mock.patch("core.changelog._read_commits", return_value=commits):
            sections = changelog.collect_sections(
                range_spec="HEAD", previous_text=previous_text
            )

        self.assertEqual(len(sections), 2)
        unreleased, latest = sections
        self.assertEqual(
            unreleased.entries,
            ["- " + "d" * 8 + " Fix changelog regression (#604)"],
        )
        self.assertEqual(latest.title, "v1.3.0 (2025-10-06)")
        self.assertEqual(
            latest.entries,
            ["- " + "b" * 8 + " Handle changelog duplicate merges (#601)"],
        )

    def test_retry_release_reopens_latest_section(self):
        previous_text = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "v1.3.0 (2025-10-06)",
                "-------------------",
                "",
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "- " + "c" * 8 + " Improve release retry messaging (#600)",
                "",
            ]
        )

        commits_without_release = [
            changelog.Commit(
                sha="d" * 40,
                date="2025-10-07",
                subject="Add retry fallback coverage (#603)",
            ),
            changelog.Commit(
                sha="b" * 40,
                date="2025-10-05",
                subject="Handle changelog duplicate merges (#601)",
            ),
            changelog.Commit(
                sha="c" * 40,
                date="2025-10-04",
                subject="Improve release retry messaging (#600)",
            ),
        ]

        with mock.patch(
            "core.changelog._read_commits", return_value=commits_without_release
        ):
            reopened_sections = changelog.collect_sections(
                range_spec="HEAD", previous_text=previous_text, reopen_latest=True
            )

        self.assertEqual(len(reopened_sections), 1)
        unreleased = reopened_sections[0]
        self.assertIsNone(unreleased.version)
        self.assertEqual(
            unreleased.entries,
            [
                "- " + "d" * 8 + " Add retry fallback coverage (#603)",
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "- " + "c" * 8 + " Improve release retry messaging (#600)",
            ],
        )

        reopened_text = changelog.render_changelog(reopened_sections)

        commits_with_release = [
            changelog.Commit(sha="a" * 40, date="2025-10-08", subject="Release v1.3.0"),
            changelog.Commit(
                sha="d" * 40,
                date="2025-10-07",
                subject="Add retry fallback coverage (#603)",
            ),
            changelog.Commit(
                sha="b" * 40,
                date="2025-10-05",
                subject="Handle changelog duplicate merges (#601)",
            ),
            changelog.Commit(
                sha="c" * 40,
                date="2025-10-04",
                subject="Improve release retry messaging (#600)",
            ),
        ]

        with mock.patch("core.changelog._read_commits", return_value=commits_with_release):
            retry_sections = changelog.collect_sections(
                range_spec="HEAD", previous_text=reopened_text
            )

        self.assertEqual(len(retry_sections), 2)
        retry_unreleased, retry_release = retry_sections
        self.assertEqual(retry_unreleased.entries, [])
        self.assertEqual(retry_release.title, "v1.3.0 (2025-10-08)")
        self.assertEqual(
            retry_release.entries,
            [
                "- " + "d" * 8 + " Add retry fallback coverage (#603)",
                "- " + "b" * 8 + " Handle changelog duplicate merges (#601)",
                "- " + "c" * 8 + " Improve release retry messaging (#600)",
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

    def test_determine_range_spec_uses_previous_changelog_when_tags_missing(self):
        previous_text = "\n".join(
            [
                "Changelog",
                "=========",
                "",
                "Unreleased",
                "----------",
                "",
                "- " + "b" * 8 + " Existing entry",
                "",
                "v1.3.0 (2025-10-06)",
                "-------------------",
                "",
                "- " + "a" * 8 + " Example entry",
                "",
            ]
        )

        release_commit = "f" * 40

        def fake_run(cmd, capture_output=False, text=False, check=False):
            if cmd == ["git", "describe", "--tags", "--exact-match", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            if cmd == ["git", "describe", "--tags", "--abbrev=0"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            if (
                cmd[:3] == ["git", "log", "--max-count=1"]
                and "--fixed-strings" in cmd
                and cmd[-1] == "--grep=Release v1.3.0"
            ):
                return subprocess.CompletedProcess(cmd, 0, stdout=release_commit + "\n", stderr="")
            if (
                cmd[:3] == ["git", "log", "--max-count=1"]
                and "--fixed-strings" in cmd
            ):
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        with mock.patch("core.changelog.subprocess.run", side_effect=fake_run):
            self.assertEqual(
                changelog.determine_range_spec(previous_text=previous_text),
                f"{release_commit}..HEAD",
            )
