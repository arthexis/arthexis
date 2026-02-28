"""allauth adapter customizations for project signup policy."""

from allauth.account.adapter import DefaultAccountAdapter


class ClosedSignupAccountAdapter(DefaultAccountAdapter):
    """Disallow open signup; onboarding remains invitation-driven."""

    def is_open_for_signup(self, request):
        """Return False so allauth signup routes cannot create accounts."""

        return False
