from utils.role_app_profiles import (
    RETIRED_RUNTIME_APP_SELECTORS,
    ROLE_DEFAULT_APP_SELECTORS,
    RoleProfile,
    filter_retired_app_selectors,
    get_role_default_app_selectors,
)


def test_nginx_is_retired_from_role_profiles():
    assert "apps.nginx" in RETIRED_RUNTIME_APP_SELECTORS
    assert "apps.nginx" not in ROLE_DEFAULT_APP_SELECTORS[RoleProfile.WATCHTOWER]
    assert "apps.nginx" not in get_role_default_app_selectors(RoleProfile.WATCHTOWER)
    assert filter_retired_app_selectors(("apps.nginx", "apps.core")) == ("apps.core",)
