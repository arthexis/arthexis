from apps.core.versioning import (
    AUTO_UPGRADE_DAY_MINUTES,
    AUTO_UPGRADE_MONTH_MINUTES,
    AUTO_UPGRADE_WEEK_MINUTES,
    UPGRADE_CHANNEL_CUSTOM,
    UPGRADE_CHANNEL_REGULAR,
    UPGRADE_CHANNEL_STABLE,
    UPGRADE_CHANNEL_UNSTABLE,
    VERSION_BUMP_MAJOR,
    VERSION_BUMP_MINOR,
    VERSION_BUMP_NONE,
    VERSION_BUMP_PATCH,
    VERSION_BUMP_UNKNOWN,
    auto_upgrade_bump_allowed,
    auto_upgrade_bump_cadence_minutes,
    classify_version_bump,
    normalize_upgrade_channel,
)


def test_classify_version_bump_handles_semver_tiers():
    assert classify_version_bump("1.2.3", "1.2.4") == VERSION_BUMP_PATCH
    assert classify_version_bump("1.2.3", "1.3.0") == VERSION_BUMP_MINOR
    assert classify_version_bump("1.2.3", "2.0.0") == VERSION_BUMP_MAJOR
    assert classify_version_bump("1.2.3", "1.2.3") == VERSION_BUMP_NONE
    assert classify_version_bump("1.2.3", "1.2.2") == VERSION_BUMP_UNKNOWN


def test_normalize_upgrade_channel_aliases():
    assert normalize_upgrade_channel("lts") == UPGRADE_CHANNEL_STABLE
    assert normalize_upgrade_channel("normal") == UPGRADE_CHANNEL_REGULAR
    assert normalize_upgrade_channel("version") == UPGRADE_CHANNEL_REGULAR
    assert normalize_upgrade_channel("latest") == UPGRADE_CHANNEL_UNSTABLE
    assert normalize_upgrade_channel("custom") == UPGRADE_CHANNEL_CUSTOM


def test_auto_upgrade_bump_rules_match_channel_tiers():
    assert auto_upgrade_bump_allowed("stable", VERSION_BUMP_PATCH) is True
    assert auto_upgrade_bump_allowed("stable", VERSION_BUMP_MINOR) is True
    assert auto_upgrade_bump_allowed("stable", VERSION_BUMP_MAJOR) is False
    assert auto_upgrade_bump_allowed("regular", VERSION_BUMP_MAJOR) is True

    assert (
        auto_upgrade_bump_cadence_minutes("stable", VERSION_BUMP_PATCH)
        == AUTO_UPGRADE_WEEK_MINUTES
    )
    assert (
        auto_upgrade_bump_cadence_minutes("stable", VERSION_BUMP_MINOR)
        == AUTO_UPGRADE_MONTH_MINUTES
    )
    assert (
        auto_upgrade_bump_cadence_minutes("regular", VERSION_BUMP_MINOR)
        == AUTO_UPGRADE_DAY_MINUTES
    )
    assert (
        auto_upgrade_bump_cadence_minutes("regular", VERSION_BUMP_MAJOR)
        == AUTO_UPGRADE_WEEK_MINUTES
    )
