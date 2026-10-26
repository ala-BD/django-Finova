"""IA et fonctionnalités ajoutées : explication du risque, suggestion d'échéancier,
déclaration en langage naturel, graphique des soldes, exports PDF et calendrier."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.ai import LLMError
from settlements import nlp, risk, services
from settlements.models import RepaymentPlan, Settlement

from .fixtures import SettlementFixturesMixin
from .test_ai import TYPICAL_HIGH

D = Decimal
MEMBERS = [(1, ["Ala", "Ben Salah"]), (2, ["Yahya", "Trabelsi"]), (3, ["Linda", "Touil"])]


def url(name, group, **kwargs):
    return reverse(f"settlements:{name}", kwargs={"gid": group.pk, **kwargs})


class ExplainTests(TestCase):
    def test_contributions_sorted_and_signed(self):
        items = risk.explain(TYPICAL_HIGH)
        self.assertEqual({i["feature"] for i in items}, set(risk.FEATURES))
        magnitudes = [abs(i["contribution"]) for i in items]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))
        self.assertEqual(items[0]["share"], 100)
        past = next(i for i in items if i["feature"] == "past_late_count")
        self.assertTrue(past["raises"])  # 4 retards passés : bien au-dessus de la moyenne


class RankPlansTests(TestCase):
    base = {name: TYPICAL_HIGH[name] for name in risk.FEATURES if name != "installments_count"}

    def test_candidates_are_affordable_and_sorted(self):
        ranked = risk.rank_plans(self.base, monthly_capacity=600)
        self.assertTrue(ranked)
        self.assertTrue(all(c["monthly_load"] <= 600 for c in ranked))
        probabilities = [round(c["probability"], 3) for c in ranked]
        self.assertEqual(probabilities, sorted(probabilities))

    def test_no_candidate_when_budget_too_small(self):
        self.assertEqual(risk.rank_plans(self.base, monthly_capacity=10), [])


class NaturalLanguageRulesTests(TestCase):
    def parse(self, text, members=MEMBERS):
        return nlp.parse_rule_based(text, members)

    def test_full_sentence(self):
        result = self.parse("J'ai remboursé 30 dinars à Ala hier en espèces")
        self.assertEqual((result["receiver"], result["amount"], result["note"]), (1, D("30.000"), "Espèces"))

    def test_decimal_comma_typo_and_purpose(self):
        result = self.parse("Rendu 9,5 DT à Yahia pour le taxi par virement")
        self.assertEqual((result["receiver"], result["amount"]), (2, D("9.500")))
        self.assertEqual(result["note"], "Pour taxi · Virement")

    def test_date_is_not_an_amount(self):
        self.assertEqual(self.parse("Le 12/10 j'ai remboursé 35 DT à Linda")["amount"], D("35.000"))

    def test_ambiguous_first_name_is_left_empty(self):
        members = [(1, ["Ala", "Ben Salah"]), (2, ["Ala", "Mejri"])]
        self.assertIsNone(self.parse("20 dinars à Ala", members)["receiver"])

    def test_nothing_found(self):
        result = self.parse("merci pour hier")
        self.assertEqual((result["receiver"], result["amount"]), (None, None))


class NaturalLanguageLLMTests(TestCase):
    """call_llm simulé : aucun appel réseau."""

    @patch("settlements.nlp.call_llm", return_value='{"receiver_id": 3, "amount": 47.3, "note": "Virement"}')
    def test_valid_llm_json(self, _):
        result = nlp.parse_repayment("j'ai envoyé quarante-sept dinars trente à Linda", MEMBERS)
        self.assertEqual((result["receiver"], result["amount"], result["source"]), (3, D("47.300"), "llm"))

    @patch("settlements.nlp.call_llm", return_value='{"receiver_id": 99, "amount": 30, "note": ""}')
    def test_unknown_member_id_is_rejected_and_completed_by_rules(self, _):
        result = nlp.parse_repayment("30 dinars à Ala", MEMBERS)
        self.assertEqual((result["receiver"], result["source"]), (1, "llm+regles"))

    @patch("settlements.nlp.call_llm", side_effect=LLMError("timeout"))
    def test_api_error_falls_back_to_rules(self, _):
        result = nlp.parse_repayment("30 dinars à Ala", MEMBERS)
        self.assertEqual((result["receiver"], result["source"]), (1, "regles"))

    @patch("settlements.nlp.call_llm", return_value="désolé, je ne sais pas")
    def test_non_json_falls_back_to_rules(self, _):
        self.assertEqual(nlp.parse_repayment("30 dinars à Ala", MEMBERS)["source"], "regles")

    def test_negative_or_huge_amount_refused(self):
        self.assertIsNone(nlp.to_amount("-5"))
        self.assertIsNone(nlp.to_amount("99999999"))


class FeatureViewsTests(SettlementFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.settlement_obj = self.settlement(amount="300.000")

    def make_plan(self):
        plan = RepaymentPlan(settlement=self.settlement_obj, installments_count=3,
                             start_date=timezone.localdate() + timedelta(days=7))
        return services.save_plan(plan)

    @override_settings(LLM_API_KEY="")
    def test_parse_view_uses_rules_without_api_key(self):
        self.client.force_login(self.yahya)
        with patch("settlements.nlp.call_llm") as call_llm:
            response = self.client.post(url("parse", self.group), {"text": "J'ai rendu 30 dinars à Linda en cash"})
        call_llm.assert_not_called()
        data = response.json()
        self.assertEqual((data["receiver"], data["amount"], data["source"]), (self.m_linda.pk, "30.000", "regles"))
        self.assertEqual(data["receiver_label"], "Linda")

    def test_parse_view_never_proposes_the_payer(self):
        self.client.force_login(self.yahya)
        data = self.client.post(url("parse", self.group), {"text": "20 dinars à Yahya"}).json()
        self.assertIsNone(data["receiver"])
        self.assertIn("bénéficiaire", data["missing"])

    def test_parse_view_validation_and_access(self):
        self.client.force_login(self.yahya)
        self.assertEqual(self.client.post(url("parse", self.group), {"text": ""}).status_code, 400)
        self.client.force_login(self.imem)
        self.assertEqual(self.client.post(url("parse", self.group), {"text": "5 dt à Ala"}).status_code, 403)

    def test_suggest_view(self):
        self.client.force_login(self.yahya)
        response = self.client.post(url("plan_suggest", self.group, sid=self.settlement_obj.pk), {"capacity": "120"})
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(data["candidates"]), 3)
        self.assertTrue(all(c["monthly_load"] <= 120 for c in data["candidates"]))
        self.assertIn("default_percent", data)

    def test_suggest_view_rejects_bad_capacity_and_third_member(self):
        self.client.force_login(self.yahya)
        bad = self.client.post(url("plan_suggest", self.group, sid=self.settlement_obj.pk), {"capacity": "0"})
        self.assertEqual(bad.status_code, 400)
        self.client.force_login(self.linda)
        forbidden = self.client.post(url("plan_suggest", self.group, sid=self.settlement_obj.pk), {"capacity": "50"})
        self.assertEqual(forbidden.status_code, 403)

    def test_detail_shows_risk_explanation(self):
        self.make_plan()
        self.client.force_login(self.yahya)
        response = self.client.get(url("detail", self.group, sid=self.settlement_obj.pk))
        self.assertContains(response, "Pourquoi ce score ?")
        self.assertEqual(len(response.context["explanation"]), len(risk.FEATURES))

    def test_balances_chart_data(self):
        Settlement.objects.create(group=self.group, payer=self.m_linda, receiver=self.m_ala,
                                  amount=D("40"), status=Settlement.Status.CONFIRMED)
        self.client.force_login(self.ala)
        response = self.client.get(url("balances", self.group))
        chart = response.context["chart_data"]
        self.assertEqual(dict(zip(chart["labels"], chart["values"]))["Linda"], 40.0)
        self.assertContains(response, 'id="balances-data"')

    def test_pdf_export(self):
        self.make_plan()
        self.client.force_login(self.ala)
        response = self.client.get(url("plan_pdf", self.group, sid=self.settlement_obj.pk))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_ics_export(self):
        plan = self.make_plan()
        plan.installments.first().register_payment(D("100"))
        self.client.force_login(self.yahya)
        response = self.client.get(url("plan_ics", self.group, sid=self.settlement_obj.pk))
        body = response.content.decode()
        self.assertTrue(response["Content-Type"].startswith("text/calendar"))
        self.assertEqual(body.count("BEGIN:VEVENT"), 2)  # l'échéance payée n'y figure pas
        self.assertIn("\r\n", body)
        self.assertTrue(all(len(line.encode()) <= 75 for line in body.split("\r\n")))
        due = plan.installments.last().due_date
        self.assertIn(f"DTSTART;VALUE=DATE:{due:%Y%m%d}", body)

    def test_exports_access(self):
        self.client.force_login(self.ala)
        self.assertEqual(self.client.get(url("plan_pdf", self.group, sid=self.settlement_obj.pk)).status_code, 404)
        self.make_plan()
        self.client.force_login(self.imem)
        self.assertEqual(self.client.get(url("plan_ics", self.group, sid=self.settlement_obj.pk)).status_code, 403)
