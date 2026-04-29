from django.contrib import admin
from django.test import SimpleTestCase

from apps.library.models import (
    KindleLibraryTransfer,
    OwnerLibraryHolding,
    PublicLibraryEntry,
    RegisteredKindle,
)

class LibraryAdminRegistrationTests(SimpleTestCase):
    def test_library_models_are_registered_in_admin(self):
        for model in (
            RegisteredKindle,
            PublicLibraryEntry,
            OwnerLibraryHolding,
            KindleLibraryTransfer,
        ):
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site.is_registered(model))
