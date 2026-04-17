from types import SimpleNamespace

from apps.sites.templatetags import admin_extras


class _Model:
    def __init__(self, app_label, model_name):
        self._meta = SimpleNamespace(app_label=app_label, model_name=model_name)


class _ModelAdmin:
    def __init__(self, model):
        self.model = model


def test_admin_profile_url_falls_back_when_teams_user_not_resolvable(monkeypatch):
    teams_model = _Model("teams", "user")
    core_model = _Model("core", "user")
    user = SimpleNamespace(pk=42)

    registry = {
        teams_model: _ModelAdmin(teams_model),
        core_model: _ModelAdmin(core_model),
    }

    def fake_get_model(app_label, model_name):
        if (app_label, model_name) == ("teams", "User"):
            return teams_model
        if (app_label, model_name) == ("core", "User"):
            return core_model
        raise LookupError

    def fake_admin_model_instance(model_admin, request, candidate_user):
        if model_admin.model is teams_model:
            return None
        return candidate_user

    monkeypatch.setattr(admin_extras.apps, "get_model", fake_get_model)
    monkeypatch.setattr(admin_extras.admin.site, "_registry", registry)
    monkeypatch.setattr(admin_extras, "_admin_model_instance", fake_admin_model_instance)
    monkeypatch.setattr(admin_extras, "_admin_has_access", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        admin_extras,
        "_admin_change_url",
        lambda model, candidate_user: f"/admin/{model._meta.app_label}/{model._meta.model_name}/{candidate_user.pk}/change/",
    )

    url = admin_extras.admin_profile_url({"request": object()}, user)

    assert url == "/admin/core/user/42/change/"
