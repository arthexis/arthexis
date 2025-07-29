from django.test import SimpleTestCase
from django.urls import URLResolver, path

from .utils import footer_link, get_footer_columns


@footer_link("Sample", column="Info")
def sample_view(request):
    pass

urlpatterns = [path("sample/", sample_view, name="sample")]


class FooterUtilsTests(SimpleTestCase):
    def test_collects_footer_links(self):
        resolver = URLResolver("", urlpatterns)
        columns = get_footer_columns(resolver)
        self.assertEqual(columns, [{"name": "Info", "links": [{"name": "Sample", "path": "/sample/"}]}])
