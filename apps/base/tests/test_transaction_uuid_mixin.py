import uuid

import pytest
from django.core.exceptions import ValidationError

from apps.links.models import Reference


@pytest.mark.django_db
def test_transaction_uuid_is_immutable():
    reference = Reference.objects.create(alt_text="Immutable", value="")
    original_uuid = reference.transaction_uuid

    reference.transaction_uuid = uuid.uuid4()

    with pytest.raises(ValidationError):
        reference.save()

    reference.refresh_from_db()
    assert reference.transaction_uuid == original_uuid
