from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from analytics import services
from analytics.datasource import get_user_expenses
from analytics.demo import CATEGORIES
from analytics.exports import CSV_HEADER, build_csv, build_pdf
from analytics.tests.helpers import record, sample_records
from core.ai import LLMError

OCTOBER = [
    record(title="Loyer", amount="900", date=date(2026, 10, 1), category="Logement"),
    record(title="Courses", amount="100", date=date(2026, 10, 3)),
    record(title="Taxi", amount="20", date=date(2026, 10, 4), category="Transport"),
    record(title="Courses", amount="80", date=date(2026, 9, 3)),
    record(title="Loyer", amount="900", date=date(2026, 9, 1), category="Logement"),
]


class FilterTests(SimpleTestCase):
    def test_each_criterion(self):
        records = sample_records()
        cases = {
            "start": ({"start": "2026-10-01"}, lambda r: r.date >= date(2026, 10, 1)),
            "end": ({"end": "2026-09-30"}, lambda r: r.date <= date(2026, 9, 30)),
            "group": ({"group": "2"}, lambda r: r.group_id == 2),
            "category": ({"category": "Loisirs"}, lambda r: r.category == "Loisirs"),
            "member": ({"member": "Mariem"}, lambda r: r.payer == "Mariem"),
            "min": ({"min_amount": "60"}, lambda r: r.amount >= 60),
            "max": ({"max_amount": "20"}, lambda r: r.amount <= 20),
        }
        for name, (params, predicate) in cases.items():
            with self.subTest(name):
                expected = [r for r in records if predicate(r)]
                self.assertTrue(expected)
                self.assertEqual(services.apply_filters(records, params), expected)

    def test_criteria_combine(self):
        result = services.apply_filters(sample_records(), {"category": "Loisirs", "member": "Mariem"})
        self.assertEqual([r.id for r in result], [5])

    def test_params_to_json_drops_empty_values(self):
        params = services.params_to_json(
            {"start": date(2026, 10, 1), "end": None, "category": "", "min_amount": Decimal("5")}
        )
        self.assertEqual(params, {"start": "2026-10-01", "min_amount": "5"})

    def test_describe_params(self):
        text = services.describe_params({"start": "2026-10-01", "group": "2"}, {"2": "Paris"})
        self.assertEqual(text, "du 01/10/2026 au …, groupe Paris")
        self.assertEqual(services.describe_params({}), "toutes les dépenses")


class AggregateTests(SimpleTestCase):
    def test_dashboard_data(self):
        data = services.dashboard_data(OCTOBER, CATEGORIES)
        self.assertEqual(data["total"], Decimal("2000"))
        self.assertEqual(data["count"], 5)
        self.assertEqual(data["by_category"][0]["label"], "Logement")
        self.assertEqual(data["by_category"][0]["share"], Decimal("90.0"))
        self.assertEqual([m["key"] for m in data["by_month"]], ["2026-09", "2026-10"])
        # La couleur suit la catégorie (emplacement fixe), pas son rang.
        self.assertEqual(data["charts"]["category"]["slots"][0], CATEGORIES.index("Logement"))

    def test_month_aggregates(self):
        agg = services.month_aggregates(OCTOBER, "2026-10", "TND")
        self.assertEqual(agg["total"], Decimal("1020.000"))
        self.assertEqual(agg["previous_total"], Decimal("980.000"))
        self.assertEqual(agg["variation_pct"], Decimal("4.1"))
        self.assertEqual(agg["biggest"]["title"], "Loyer")
        self.assertEqual(agg["month_label"], "octobre 2026")

    def test_month_without_previous_month(self):
        agg = services.month_aggregates(OCTOBER, "2026-09", "TND")
        self.assertIsNone(agg["variation_pct"])
        self.assertIn("septembre 2026", services.template_summary(agg))

    def test_january_previous_month_is_december(self):
        agg = services.month_aggregates([record(date=date(2027, 1, 5))], "2027-01", "TND")
        self.assertEqual(agg["previous_month_label"], "décembre 2026")

    def test_format_number(self):
        self.assertEqual(services.format_number(Decimal("1234.5")), "1 234,500")
        self.assertEqual(services.format_money(Decimal("7"), "EUR"), "7,000 EUR")


class SummaryTests(TestCase):
    def setUp(self):
        self.agg = services.month_aggregates(OCTOBER, "2026-10", "TND")

    def test_template_summary_only_uses_aggregate_numbers(self):
        text = services.template_summary(self.agg)
        self.assertIn("1 020,000 TND", text)
        self.assertIn("hausse de 4,1 %", text)
        self.assertEqual(services.unverified_numbers(text, self.agg), [])

    def test_prompt_contains_french_formatted_aggregates_not_raw_expenses(self):
        prompt = services.build_prompt(self.agg)
        self.assertIn('"total_du_mois": "1 020 TND"', prompt)
        self.assertIn("hausse de 4,1 %", prompt)
        self.assertIn("01/10/2026", prompt)
        self.assertIn('"montant": "900 TND"', prompt)  # montant rond : pas de « 900,000 » ambigu
        self.assertNotIn("Taxi", prompt)

    def test_prompt_keeps_only_top_categories(self):
        records = [record(category=name, amount=str(10 * (i + 1))) for i, name in enumerate(CATEGORIES)]
        payload = services.llm_payload(services.month_aggregates(records, "2026-10", "TND"))
        self.assertEqual(len(payload["principales_categories"]), services.PROMPT_TOP_CATEGORIES)

    def test_current_month_is_flagged_and_its_date_allowed(self):
        agg = services.month_aggregates(OCTOBER, "2026-10", "TND", today=date(2026, 10, 26))
        self.assertIn("mois en cours, au 26/10/2026", services.template_summary(agg))
        self.assertIn("mois_en_cours", services.llm_payload(agg))
        self.assertEqual(services.unverified_numbers("Chiffres arrêtés au 26/10/2026.", agg), [])
        past = services.month_aggregates(OCTOBER, "2026-09", "TND", today=date(2026, 10, 26))
        self.assertIsNone(past["as_of"])

    @mock.patch("analytics.services.call_llm")
    def test_llm_summary_is_used_when_numbers_match(self, call_llm):
        call_llm.return_value = (
            "En octobre 2026, vous avez dépensé 1 020,000 TND, soit 4,1 % de plus qu'en septembre "
            "(980 TND). Le loyer de 900 TND représente 88,2 % du total."
        )
        result = services.generate_narrative_summary(self.agg)
        self.assertEqual(result["source"], "llm")
        call_llm.assert_called_once()
        self.assertEqual(call_llm.call_args.kwargs["module"], "analytics")

    @mock.patch("analytics.services.call_llm")
    def test_invented_number_falls_back_to_template(self, call_llm):
        call_llm.return_value = "En octobre 2026, vous avez dépensé 1 500 TND."
        result = services.generate_narrative_summary(self.agg)
        self.assertEqual(result["source"], "template")
        self.assertIn("1 500", result["reason"])
        self.assertEqual(result["text"], services.template_summary(self.agg))

    @mock.patch("analytics.services.call_llm", side_effect=LLMError("LLM_API_KEY manquante"))
    def test_llm_error_falls_back_to_template(self, call_llm):
        result = services.generate_narrative_summary(self.agg)
        self.assertEqual(result["source"], "template")
        self.assertIn("IA indisponible", result["reason"])

    @mock.patch("analytics.services.call_llm", return_value={"text": "  "})
    def test_empty_llm_answer_falls_back(self, call_llm):
        self.assertEqual(services.generate_narrative_summary(self.agg)["source"], "template")

    @mock.patch("analytics.services.call_llm")
    def test_empty_month_does_not_call_llm(self, call_llm):
        agg = services.month_aggregates(OCTOBER, "2026-05", "TND")
        self.assertEqual(services.generate_narrative_summary(agg)["source"], "template")
        call_llm.assert_not_called()

    def test_number_parsing(self):
        cases = {"1 234,500": "1234.500", "1,234.50": "1234.50", "4,1": "4.1", "980": "980", "1.234.567": "1234567"}
        for raw, expected in cases.items():
            with self.subTest(raw):
                self.assertEqual(services._parse_number(raw), Decimal(expected))


class ExportTests(SimpleTestCase):
    def test_csv(self):
        content = build_csv(OCTOBER).decode("utf-8")
        self.assertTrue(content.startswith("﻿"))
        lines = content.lstrip("﻿").splitlines()
        self.assertEqual(lines[0], ";".join(CSV_HEADER))
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[1], "2026-09-01;Colocation;Loyer;Logement;Sami;900,000;TND")

    def test_pdf(self):
        content = build_pdf(OCTOBER, title="Octobre", description="tout", summary="Résumé.", currency="TND")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_pdf_without_expenses(self):
        self.assertTrue(build_pdf([], title="Vide", description="", summary="", currency="TND").startswith(b"%PDF"))


class DataSourceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zahra", password="un-mot-de-passe-fort", first_name="Zahra")

    @mock.patch("analytics.datasource._load_real_expenses", side_effect=ImportError)
    def test_demo_data_while_m1_m2_are_missing(self, _):
        source = get_user_expenses(self.user)
        self.assertTrue(source.is_demo)
        self.assertEqual(len(source.records), 240)
        self.assertIn("Zahra", {r.payer for r in source.records})

    @override_settings(ANALYTICS_DEMO_DATA=False)
    @mock.patch("analytics.datasource._load_real_expenses", side_effect=NotImplementedError)
    def test_no_demo_data_when_disabled(self, _):
        source = get_user_expenses(self.user)
        self.assertEqual((source.records, source.is_demo), ([], False))

    @mock.patch("analytics.datasource._load_real_expenses", return_value=OCTOBER)
    def test_real_data_when_available(self, _):
        source = get_user_expenses(self.user)
        self.assertFalse(source.is_demo)
        self.assertEqual(source.records, OCTOBER)
