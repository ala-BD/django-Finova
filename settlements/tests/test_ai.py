from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.ai import LLMError
from core.models import AIRequestLog
from settlements import risk, services
from settlements.models import RepaymentPlan, RiskPrediction

from .fixtures import SettlementFixturesMixin

D = Decimal

TYPICAL_LOW = {
    "amount": 40, "installments_count": 2, "past_late_count": 0,
    "avg_confirmation_delay_days": 0.5, "amount_weight_ratio": 20, "days_since_creation": 60,
}
TYPICAL_HIGH = {
    "amount": 1800, "installments_count": 10, "past_late_count": 4,
    "avg_confirmation_delay_days": 6, "amount_weight_ratio": 1800, "days_since_creation": 2,
}


class RiskModelTests(TestCase):
    """Le modèle sérialisé (ml/m3_risk_model.joblib) est chargé tel qu'en production."""

    def test_typical_cases(self):
        low, version, latency_ms = risk.predict_probability(TYPICAL_LOW)
        high, _, _ = risk.predict_probability(TYPICAL_HIGH)
        self.assertLess(low, risk.THRESHOLD)
        self.assertGreater(high, risk.THRESHOLD)
        self.assertTrue(version.startswith("logreg-"))
        self.assertLess(latency_ms, 200)  # cible de la soutenance

    def test_more_past_delays_means_higher_risk(self):
        base, _, _ = risk.predict_probability(TYPICAL_LOW)
        worse, _, _ = risk.predict_probability({**TYPICAL_LOW, "past_late_count": 3})
        self.assertGreater(worse, base)


class RiskServiceTests(SettlementFixturesMixin, TestCase):
    def make_plan(self, settlement, start):
        plan = RepaymentPlan.objects.create(
            settlement=settlement, total_amount=settlement.amount,
            installments_count=2, start_date=start,
        )
        plan.generate_installments()
        return plan

    def test_features_count_past_delays_of_the_debtor(self):
        old = self.make_plan(self.settlement(amount="50"), start=date.today() - timedelta(days=60))
        current = self.make_plan(self.settlement(amount="120"), start=date.today() + timedelta(days=7))
        self.assertTrue(all(i.is_late() for i in old.installments.all()))

        features = risk.build_features(current)

        self.assertEqual(features["past_late_count"], 2)
        self.assertEqual(features["installments_count"], 2)
        self.assertEqual(features["amount_weight_ratio"], 60.0)  # 120 / poids 2 de Yahya
        self.assertEqual(features["avg_confirmation_delay_days"], risk.DEFAULT_CONFIRMATION_DELAY)
        self.assertEqual(list(features), risk.FEATURES)

    def test_predict_risk_stores_prediction_and_latency_log(self):
        plan = self.make_plan(self.settlement(), start=date.today() + timedelta(days=7))
        prediction = services.predict_risk(plan)
        self.assertIsInstance(prediction, RiskPrediction)
        self.assertIn(prediction.label, RiskPrediction.Label.values)
        self.assertTrue(AIRequestLog.objects.filter(feature="m3.risk", success=True).exists())

    def test_missing_model_is_not_blocking(self):
        plan = self.make_plan(self.settlement(), start=date.today() + timedelta(days=7))
        with patch.object(risk, "load_model", side_effect=risk.RiskModelUnavailable("absent")):
            self.assertIsNone(services.predict_risk(plan))


class ReminderServiceTests(SettlementFixturesMixin, TestCase):
    """Aucun appel réseau : call_llm est simulé."""

    def setUp(self):
        super().setUp()
        self.settlement_obj = self.settlement(amount="75.000")

    @patch("settlements.services.call_llm", return_value="Bonjour Yahya, il reste 75,000 TND à rembourser.")
    def test_llm_message_used_when_valid(self, call_llm):
        result = services.generate_reminder(self.settlement_obj, "neutral", user=self.ala)
        self.assertEqual(result["source"], "llm")
        prompt = call_llm.call_args.args[1]
        self.assertIn("ton neutre", prompt)
        self.assertIn("75 TND", prompt)  # jamais « 75.000 », lu « 75 000 » en français

    @patch("settlements.services.call_llm", side_effect=LLMError("timeout"))
    def test_template_fallback_on_api_error(self, _):
        result = services.generate_reminder(self.settlement_obj, "firm")
        self.assertEqual(result["source"], "template")
        self.assertIn("75 TND", result["message"])
        self.assertIn("Yahya", result["message"])

    @patch("settlements.services.call_llm", return_value="Rembourse vite.")
    def test_template_fallback_when_amount_missing(self, _):
        self.assertEqual(services.generate_reminder(self.settlement_obj, "friendly")["source"], "template")

    @patch("settlements.services.call_llm", return_value="Il reste 75 000 TND à rembourser.")
    def test_template_fallback_when_amount_is_wrong(self, _):
        # Bug vu en conditions réelles : « 100.000 » (millimes) compris « 100 000 » par le LLM
        self.assertEqual(services.generate_reminder(self.settlement_obj, "neutral")["source"], "template")

    def test_human_amount_and_amount_extraction(self):
        self.assertEqual(services.human_amount(D("100.000")), "100")
        self.assertEqual(services.human_amount(D("17.500")), "17,5")
        self.assertEqual(services.human_amount(D("0.250")), "0,25")
        self.assertEqual(
            services.amounts_in_text("Reste 1 200,5 TND sur 75,000 et 100 000 dinars, soit 12.5"),
            [D("1200.5"), D("75.000"), D("100000"), D("12.5")],
        )

    @patch("settlements.services.call_llm", return_value="75 " + "x" * 700)
    def test_template_fallback_when_too_long(self, _):
        self.assertEqual(services.generate_reminder(self.settlement_obj, "friendly")["source"], "template")

    def test_unknown_tone_rejected(self):
        with self.assertRaises(ValidationError):
            services.generate_reminder(self.settlement_obj, "rude")

    def test_reminder_uses_remaining_amount_and_delay(self):
        plan = RepaymentPlan.objects.create(
            settlement=self.settlement_obj, total_amount=D("75"), installments_count=3,
            start_date=date.today() - timedelta(days=10),
        )
        plan.generate_installments()
        plan.installments.first().register_payment(D("25"), today=date.today() - timedelta(days=10))
        context = services.reminder_context(self.settlement_obj)
        self.assertEqual(context["amount"], D("50"))
        self.assertEqual(context["days_late"], 0)  # échéances suivantes pas encore dues
