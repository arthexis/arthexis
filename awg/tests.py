from django.test import TestCase
from django.urls import reverse

from .models import CableSize, ConduitFill, CalculatorTemplate


class AWGCalculatorTests(TestCase):
    def setUp(self):
        CableSize.objects.create(
            awg_size="8",
            material="cu",
            dia_in=0,
            dia_mm=0,
            area_kcmil=0,
            area_mm2=0,
            k_ohm_km=0.1,
            k_ohm_kft=0.1,
            amps_60c=55,
            amps_75c=65,
            amps_90c=75,
            line_num=1,
        )
        ConduitFill.objects.create(trade_size="1", conduit="emt", awg_8=3)

    def test_page_renders_and_calculates(self):
        url = reverse("awg:calculator")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<form")

        data = {
            "meters": "10",
            "amps": "40",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "2",
            "temperature": "60",
            "conduit": "emt",
            "ground": "1",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<table")
        self.assertContains(resp, "AWG Size</th>")
        self.assertContains(resp, "8")
        self.assertContains(resp, "Voltage Drop")
        self.assertContains(resp, "EMT")

    def test_no_cable_found(self):
        url = reverse("awg:calculator")
        data = {
            "meters": "1000",
            "amps": "200",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "2",
            "temperature": "60",
            "conduit": "emt",
            "ground": "1",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No Suitable Cable Found")


class CalculatorTemplateTests(TestCase):
    def setUp(self):
        CableSize.objects.create(
            awg_size="8",
            material="cu",
            dia_in=0,
            dia_mm=0,
            area_kcmil=0,
            area_mm2=0,
            k_ohm_km=0.1,
            k_ohm_kft=0.1,
            amps_60c=55,
            amps_75c=65,
            amps_90c=75,
            line_num=1,
        )
        ConduitFill.objects.create(trade_size="1", conduit="emt", awg_8=3)

    def test_run(self):
        tmpl = CalculatorTemplate.objects.create(
            name="test",
            meters=10,
            amps=40,
            volts=220,
            material="cu",
            max_lines=1,
            phases=2,
            temperature=60,
            conduit="emt",
            ground=1,
        )
        result = tmpl.run()
        self.assertEqual(result["awg"], "8")

    def test_get_absolute_url_prefills_form(self):
        tmpl = CalculatorTemplate.objects.create(
            name="test",
            meters=10,
            amps=40,
            volts=220,
            material="cu",
            max_lines=1,
            phases=2,
            temperature=60,
            conduit="emt",
            ground=1,
        )
        url = tmpl.get_absolute_url()
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["form"]["meters"], "10")
        self.assertIn("value=\"10\"", resp.content.decode())

    def test_all_fields_optional(self):
        from django.forms import modelform_factory

        Form = modelform_factory(CalculatorTemplate, fields="__all__")
        form = Form({})
        self.assertTrue(form.is_valid(), form.errors)
        tmpl = form.save()
        self.assertIsNone(tmpl.meters)
