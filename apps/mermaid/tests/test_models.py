import pytest
from django.db import IntegrityError

from apps.mermaid.models import Flow


@pytest.mark.django_db
class TestFlow:
    def test_str_returns_name(self):
        flow = Flow.objects.create(
            name="Sample",
            definition="graph TD; A-->B",
            description="Example diagram",
        )

        assert str(flow) == "Sample"

    def test_unique_name_enforced(self):
        Flow.objects.create(
            name="Onboarding",
            definition="graph TD; Start-->Finish",
        )

        with pytest.raises(IntegrityError):
            Flow.objects.create(
                name="Onboarding",
                definition="graph TD; X-->Y",
            )
