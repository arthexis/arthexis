from django.test import RequestFactory, TestCase
from django.views.generic import TemplateView

from core.liveupdate import LiveUpdateMixin


class DummyView(LiveUpdateMixin, TemplateView):
    template_name = "pages/base.html"
    live_update_interval = 7


class LiveUpdateMixinTests(TestCase):
    def test_mixin_sets_request_interval(self):
        factory = RequestFactory()
        request = factory.get("/")
        DummyView.as_view()(request)
        self.assertEqual(request.live_update_interval, 7)
