import os
from datetime import time
from decimal import Decimal

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from pathlib import Path
from unittest.mock import patch

from .models import (
    CableSize,
    ConduitFill,
    CalculatorTemplate,
    EnergyTariff,
    PowerLead,
)
from .views import find_conduit


class AWGCalculatorTests(TestCase):
    fixtures = [
        p.name
        for p in (Path(__file__).resolve().parent / "fixtures").glob(
            "calculator_templates__*.json"
        )
    ]

    def setUp(self):
        CableSize.objects.bulk_create(
            [
                CableSize(
                    awg_size="10",
                    material="cu",
                    dia_in=0,
                    dia_mm=0,
                    area_kcmil=0,
                    area_mm2=0,
                    k_ohm_km=0.15,
                    k_ohm_kft=0.15,
                    amps_60c=35,
                    amps_75c=90,
                    amps_90c=100,
                    line_num=1,
                ),
                CableSize(
                    awg_size="8",
                    material="cu",
                    dia_in=0,
                    dia_mm=0,
                    area_kcmil=0,
                    area_mm2=0,
                    k_ohm_km=0.4,
                    k_ohm_kft=0.4,
                    amps_60c=55,
                    amps_75c=65,
                    amps_90c=75,
                    line_num=1,
                ),
                CableSize(
                    awg_size="6",
                    material="cu",
                    dia_in=0,
                    dia_mm=0,
                    area_kcmil=0,
                    area_mm2=0,
                    k_ohm_km=0.3,
                    k_ohm_kft=0.3,
                    amps_60c=95,
                    amps_75c=105,
                    amps_90c=115,
                    line_num=1,
                ),
            ]
        )
        ConduitFill.objects.create(
            trade_size="1",
            conduit="emt",
            awg_10=4,
            awg_8=4,
            awg_6=4,
        )

    def test_page_renders_and_calculates(self):
        url = reverse("awg:calculator")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<form")
        self.assertNotIn('value="10"', resp.content.decode())
        self.assertNotIn('value="40"', resp.content.decode())
        self.assertNotIn('value="220"', resp.content.decode())
        self.assertIn("Calculate</button>", resp.content.decode())
        self.assertContains(resp, '<option value="[1]"')

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
        self.assertContains(resp, "EMT (Thin-wall)")
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
        self.assertEqual(lead.status, PowerLead.Status.OPEN)
        self.assertFalse(lead.malformed)

    def test_power_lead_uses_original_referer(self):
        url = reverse("awg:calculator")
        data = {
            "meters": "5",
            "amps": "32",
            "volts": "208",
            "material": "cu",
            "max_lines": "1",
            "phases": "2",
            "temperature": "60",
            "conduit": "emt",
            "ground": "1",
        }
        self.client.get(
            reverse("pages:index"),
            HTTP_REFERER="https://campaign.example/power",
        )
        self.client.post(
            url,
            data,
            HTTP_REFERER="http://testserver/awg/calculator/",
            HTTP_USER_AGENT="tester",
        )
        lead = PowerLead.objects.get()
        self.assertEqual(lead.referer, "https://campaign.example/power")

    def test_power_lead_stores_forwarded_for_ip(self):
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
        self.client.post(
            url,
            data,
            HTTP_X_FORWARDED_FOR="203.0.113.5, 198.51.100.20",
            REMOTE_ADDR="198.51.100.3",
        )
        lead = PowerLead.objects.get()
        self.assertEqual(lead.ip_address, "203.0.113.5")

    def test_invalid_max_awg_reports_error(self):
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
            "max_awg": "ZAP",
        }
        resp = self.client.post(url, data)
        self.assertContains(resp, "Error: Max AWG must be a valid gauge value.")
        lead = PowerLead.objects.get()
        self.assertTrue(lead.malformed)
        self.assertEqual(lead.values["max_awg"], "ZAP")

    def test_invalid_numeric_field_reports_error(self):
        url = reverse("awg:calculator")
        data = {
            "meters": "oops",
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
        self.assertContains(resp, "Error: Meters must be a whole number.")
        lead = PowerLead.objects.get()
        self.assertTrue(lead.malformed)
        self.assertEqual(lead.values["meters"], "oops")

    def test_invalid_ground_reports_error(self):
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
            "ground": "ZAP",
        }
        resp = self.client.post(url, data)
        self.assertContains(resp, "Error: Ground must be 0, 1, or [1].")
        lead = PowerLead.objects.get()
        self.assertTrue(lead.malformed)
        self.assertEqual(lead.values["ground"], "ZAP")

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

    def test_special_ground_value_reported(self):
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
            "ground": "[1]",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('value="[1]" selected', content)
        self.assertRegex(content, r"2\+[01] \(\[1\]\)")
        self.assertRegex(content, r"20\+(?:0|10) \(\[1\]\)")

    def test_optional_ground_runs_both_scenarios(self):
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
            "ground": "[1]",
        }
        seen = set()

        def fake_conduit(awg, cables, *, conduit="emt"):
            seen.add(cables)
            return {"size_inch": "1"}

        with patch("awg.views.find_conduit", side_effect=fake_conduit):
            resp = self.client.post(url, data)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen, {2, 3})

    def test_max_awg_force_emits_warning(self):
        url = reverse("awg:calculator")
        data = {
            "meters": "180",
            "amps": "60",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "3",
            "temperature": "60",
            "conduit": "emt",
            "ground": "1",
            "max_awg": "10",
        }
        resp = self.client.post(url, data)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("AWG Size", content)
        self.assertIn("10", content)
        self.assertIn("Voltage drop may exceed 3% with chosen parameters", content)
        self.assertIn("Conduit", content)

    def test_max_awg_limit_emits_warning(self):
        url = reverse("awg:calculator")
        base = {
            "meters": "300",
            "amps": "50",
            "volts": "220",
            "material": "cu",
            "max_lines": "1",
            "phases": "3",
            "temperature": "75",
            "conduit": "emt",
            "ground": "1",
        }

        baseline = self.client.post(url, base)
        self.assertContains(baseline, "AWG Size")
        self.assertIn("10", baseline.content.decode())

        limited = self.client.post(url, {**base, "max_awg": "6"})
        self.assertEqual(limited.status_code, 200)
        content = limited.content.decode()
        self.assertIn("AWG Size", content)
        self.assertIn(">6</td>", content)
        self.assertIn("Voltage drop exceeds 3% with given max_awg", content)
        self.assertIn("Conduit", content)

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
            "amps": "120",
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
        self.assertIn("Residential charging", tmpl.description)
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


class EnergyTariffCalculatorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="user", password="pw")
        self.client.force_login(self.user)

        self.tariff = EnergyTariff.objects.create(
            year=2025,
            season=EnergyTariff.Season.SUMMER,
            zone="1C",
            contract_type=EnergyTariff.ContractType.DOMESTIC,
            period=EnergyTariff.Period.BASIC,
            unit=EnergyTariff.Unit.KWH,
            start_time=time(0, 0),
            end_time=time(23, 59, 59),
            price_mxn=Decimal("0.8062"),
            cost_mxn=Decimal("0.6000"),
            notes="Residential summer block",
        )
        EnergyTariff.objects.create(
            year=2025,
            season=EnergyTariff.Season.SUMMER,
            zone="1C",
            contract_type=EnergyTariff.ContractType.DOMESTIC,
            period=EnergyTariff.Period.BASIC,
            unit=EnergyTariff.Unit.KW,
            start_time=time(0, 0),
            end_time=time(23, 59, 59),
            price_mxn=Decimal("3.0000"),
            cost_mxn=Decimal("2.5000"),
            notes="Demand charge",
        )

    def test_page_lists_only_kwh_tariffs(self):
        resp = self.client.get(reverse("awg:energy_tariff"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Energy Tariff Calculator")
        self.assertEqual(len(resp.context["tariff_options"]), 1)
        self.assertIn("0.8062", resp.content.decode())

    def test_calculates_estimated_bill(self):
        data = {
            "kwh": "150",
            "year": "2025",
            "contract_type": self.tariff.contract_type,
            "zone": self.tariff.zone,
            "season": self.tariff.season,
            "period": self.tariff.period,
            "tariff": str(self.tariff.pk),
        }
        resp = self.client.post(reverse("awg:energy_tariff"), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Estimated Bill")
        self.assertContains(resp, "$120.93 MXN")
        self.assertContains(resp, "Residential summer block")

    def test_invalid_kwh_shows_error(self):
        data = {
            "kwh": "-5",
            "year": "2025",
            "contract_type": self.tariff.contract_type,
            "zone": self.tariff.zone,
            "season": self.tariff.season,
            "period": self.tariff.period,
            "tariff": str(self.tariff.pk),
        }
        resp = self.client.post(reverse("awg:energy_tariff"), data)
        self.assertContains(resp, "greater than zero")

    def test_requires_login(self):
        self.client.logout()
        target = reverse("awg:energy_tariff")
        login_url = reverse("pages:login")
        resp = self.client.get(target)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, f"{login_url}?next={target}")
