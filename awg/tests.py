from django.test import TestCase
from django.urls import reverse
from pathlib import Path

from .models import CableSize, ConduitFill, CalculatorTemplate, PowerLead


class AWGCalculatorTests(TestCase):
    fixtures = [
        p.name
        for p in (Path(__file__).resolve().parent / "fixtures").glob(
            "calculator_templates__*.json"
        )
    ]

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
        self.assertNotIn('value="10"', resp.content.decode())
        self.assertNotIn('value="40"', resp.content.decode())
        self.assertNotIn('value="220"', resp.content.decode())
        self.assertIn("Calculate</button>", resp.content.decode())

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
        self.assertContains(resp, "Calculate Again")

    def test_power_lead_created(self):
        url = reverse("awg:calculator")
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
        self.client.post(url, data, HTTP_USER_AGENT="tester")
        lead = PowerLead.objects.get()
        self.assertEqual(lead.values["meters"], "10")
        self.assertEqual(lead.user_agent, "tester")

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

    def test_results_column_reordered_on_mobile(self):
        url = reverse("awg:calculator")
        resp = self.client.get(url)
        content = resp.content.decode()
        self.assertNotIn("order-first", content)
        self.assertNotIn("order-lg-last", content)

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
        content = resp.content.decode()
        self.assertIn("order-first", content)
        self.assertIn("order-lg-last", content)

    def test_odd_awg_displays_even_preference(self):
        CableSize.objects.create(
            awg_size="3",
            material="cu",
            dia_in=0,
            dia_mm=0,
            area_kcmil=0,
            area_mm2=0,
            k_ohm_km=0.1,
            k_ohm_kft=0.1,
            amps_60c=150,
            amps_75c=150,
            amps_90c=150,
            line_num=1,
        )
        url = reverse("awg:calculator")
        data = {
            "meters": "10",
            "amps": "80",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "2",
            "temperature": "60",
            "ground": "1",
        }
        resp = self.client.post(url, data)
        self.assertContains(resp, "2-3")

    def test_query_params_prefill_form(self):
        url = (
            reverse("awg:calculator")
            + "?meters=10&amps=40&volts=220&material=cu&max_lines=1&phases=2&temperature=60&conduit=emt&ground=1"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('value="10"', resp.content.decode())
        self.assertIn('value="40"', resp.content.decode())
        self.assertIn('value="220"', resp.content.decode())

    def test_template_links_displayed(self):
        tmpl = CalculatorTemplate.objects.create(
            name="Demo",
            meters=10,
            amps=40,
            volts=220,
            material="cu",
            max_lines=1,
            phases=2,
            temperature=60,
            conduit="emt",
            ground=1,
            show_in_pages=True,
        )
        resp = self.client.get(reverse("awg:calculator"))
        self.assertContains(resp, "Or try a pre-loaded template:")
        self.assertContains(resp, tmpl.name)
        self.assertContains(resp, tmpl.get_absolute_url().replace("&", "&amp;"))

    def test_template_url_defaults_dropdowns(self):
        tmpl = CalculatorTemplate.objects.get(pk=1)
        resp = self.client.get(tmpl.get_absolute_url())
        self.assertEqual(resp.status_code, 200)
        form = resp.context["form"]
        self.assertEqual(form["material"], "cu")
        self.assertEqual(form["max_lines"], "1")
        self.assertEqual(form["phases"], "2")
        self.assertEqual(form["ground"], "1")

    def test_ev_charger_template_values(self):
        tmpl = CalculatorTemplate.objects.get(name="EV Charger")
        self.assertEqual(tmpl.description, "Residential charging for a single EV.")
        self.assertEqual(tmpl.amps, 40)
        self.assertEqual(tmpl.volts, 220)
        self.assertEqual(tmpl.max_lines, 1)
        self.assertEqual(tmpl.temperature, 60)


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
        self.assertIn('value="10"', resp.content.decode())

    def test_get_absolute_url_omits_none_values(self):
        tmpl = CalculatorTemplate.objects.create(name="blank")
        url = tmpl.get_absolute_url()
        self.assertEqual(url, reverse("awg:calculator"))

    def test_all_fields_optional(self):
        from django.forms import modelform_factory

        Form = modelform_factory(CalculatorTemplate, fields="__all__")
        form = Form({})
        self.assertTrue(form.is_valid(), form.errors)
        tmpl = form.save()
        self.assertIsNone(tmpl.meters)

    def test_admin_form_uses_dropdowns(self):
        from django import forms as dj_forms
        from awg.admin import CalculatorTemplateForm

        form = CalculatorTemplateForm()
        self.assertIsInstance(form.fields["material"], dj_forms.ChoiceField)
        self.assertIsInstance(form.fields["material"].widget, dj_forms.Select)
        self.assertIn(("cu", "Copper (cu)"), form.fields["material"].choices)

        self.assertEqual(
            form.fields["max_lines"].choices,
            [(1, "1"), (2, "2"), (3, "3"), (4, "4")],
        )
        self.assertIsInstance(form.fields["max_lines"].widget, dj_forms.Select)

        self.assertIn((2, "AC Two Phases (2)"), form.fields["phases"].choices)
        self.assertEqual(form.fields["ground"].choices, [(1, "1"), (0, "0")])
