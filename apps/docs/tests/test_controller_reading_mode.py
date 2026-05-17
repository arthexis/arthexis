from django.test import RequestFactory

from apps.docs import views


def test_controller_query_flags_classify_full_document_mode():
    cases = {
        "controller=1": True,
        "tv=true": True,
        "ps4": True,
        "ps4=on": True,
        "controller=0": False,
        "tv=false": False,
        "ps4=off": False,
        "ps4=no": False,
    }

    for query, expected in cases.items():
        request = RequestFactory().get(f"/docs/?{query}")

        assert views._should_force_controller_full_document(request) is expected
