"""IA de risque de dépassement : variables, modèle entraîné, service et API JSON."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from budgets import ai_model, services
from budgets.forecasting import OVERRUN_FEATURES, forecast_interval, overrun_features, pace_ratio
from budgets.models import BudgetForecast

from .factories import add_member, make_budget, make_category, make_expense, make_group, make_user

OCTOBER = date(2026, 10, 1)
TODAY = date(2026, 10, 15)


class FeatureTests(SimpleTestCase):
    def test_features_match_model_inputs(self):
        features = overrun_features(100, 220, 200, 15, 30, [180, 210], [90, 100], 500, 400)
        self.assertEqual(tuple(features), OVERRUN_FEATURES)
        self.assertEqual(features["day_fraction"], 0.5)
        self.assertEqual(features["spent_ratio"], 0.5)
        self.assertEqual(features["forecast_ratio"], 1.1)
        self.assertAlmostEqual(features["pace_vs_usual"], 100 / 95)
        self.assertEqual(features["history_over_rate"], 0.5)
        self.assertEqual(features["group_pace_vs_usual"], 1.25)

    def test_pace_ratio_edge_cases(self):
        self.assertEqual(pace_ratio(0, 0), 1.0)
        self.assertEqual(pace_ratio(50, 0), 2.0)
        self.assertEqual(pace_ratio(1000, 10), 3.0)  # plafonné

    def test_interval_never_below_amount_already_spent(self):
        quantiles = {"debut": (0.5, 1.5), "milieu": (0.8, 1.2), "fin": (0.95, 1.05)}
        low, high = forecast_interval(200, 150, 0.5, quantiles)
        self.assertEqual((low, high), (Decimal("160.000"), Decimal("240.000")))
        low, _ = forecast_interval(200, 190, 0.1, quantiles)
        self.assertEqual(low, Decimal("190.000"))


class TrainedModelTests(SimpleTestCase):
    def test_versioned_model_loads_and_ranks_risk(self):
        card = ai_model.model_card()
        self.assertIsNotNone(card, "lancer python ml/m4_train_overrun.py")
        self.assertGreater(card["test"]["model"]["roc_auc"], 0.85)
        calm = overrun_features(50, 120, 300, 15, 30, [250, 260], [120, 130], 600, 600)
        tense = overrun_features(280, 420, 300, 15, 30, [320, 340], [150, 160], 1200, 600)
        self.assertLess(ai_model.predict_overrun_probability(calm), 0.2)
        self.assertGreater(ai_model.predict_overrun_probability(tense), 0.8)

    def test_missing_model_is_not_blocking(self):
        ai_model.load_overrun_model.cache_clear()
        try:
            with mock.patch("joblib.load", side_effect=FileNotFoundError):
                self.assertIsNone(ai_model.predict_overrun_probability({}))
                self.assertIsNone(ai_model.model_card())
        finally:
            ai_model.load_overrun_model.cache_clear()


class RiskServiceTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.category = make_category(self.group, "Restaurants")
        self.budget = make_budget(self.category, OCTOBER, limit="300")
        for month in (7, 8, 9):
            make_expense(self.category, "120", date(2026, month, 5))
            make_expense(self.category, "120", date(2026, month, 20))

    def test_analysis_contains_forecast_interval_and_risk(self):
        make_expense(self.category, "130", "2026-10-05")
        result = services.forecast_budget(self.budget, today=TODAY)
        self.assertEqual(result["predicted_total"], Decimal("250.000"))
        self.assertLessEqual(result["interval_low"], result["predicted_total"])
        self.assertGreaterEqual(result["interval_high"], result["predicted_total"])
        self.assertIsNotNone(result["overrun_probability"])
        self.assertIn(result["risk_level"], ("low", "medium", "high"))
        self.assertTrue(result["factors"][0].startswith("43 % du budget"))
        saved = BudgetForecast.objects.get(budget=self.budget)
        self.assertEqual(saved.overrun_probability, result["overrun_probability"])

    def test_budget_already_exceeded_is_certain(self):
        make_expense(self.category, "320", "2026-10-05")
        result = services.forecast_budget(self.budget, today=TODAY)
        self.assertEqual(result["overrun_probability"], 1.0)
        self.assertEqual(result["risk_level"], "high")

    def test_without_model_forecast_still_works(self):
        with mock.patch("budgets.ai_model.load_overrun_model", return_value=None):
            result = services.forecast_budget(self.budget, today=TODAY)
        self.assertIsNone(result["overrun_probability"])
        self.assertEqual(result["risk_level"], "unknown")
        self.assertIsNone(result["interval_low"])

    def test_risk_levels(self):
        self.assertEqual(services.risk_level(0.1, 0.4), ("low", "faible"))
        self.assertEqual(services.risk_level(0.5, 0.4), ("medium", "modéré"))
        self.assertEqual(services.risk_level(0.9, 0.4), ("high", "élevé"))

    def test_group_pace_compares_with_usual_spending(self):
        make_expense(self.category, "240", "2026-10-05")
        spent, usual = services.group_pace_inputs(self.group, OCTOBER, 15)
        self.assertEqual(spent, Decimal("240"))
        self.assertEqual(usual, Decimal("120"))


@mock.patch("budgets.services.timezone.localdate", return_value=TODAY)
class InsightViewsTests(TestCase):
    def setUp(self):
        self.admin = make_user()
        self.group = make_group(admin=self.admin)
        self.member = add_member(self.group).user
        self.category = make_category(self.group, "Restaurants")
        self.budget = make_budget(self.category, OCTOBER, limit="300")
        make_expense(self.category, "100", "2026-10-03")

    def url(self, name, **kwargs):
        return reverse(f"budgets:{name}", kwargs={"gid": self.group.pk, **kwargs})

    def test_insights_page_shows_risk_and_model_card(self, _):
        self.client.force_login(self.member)
        response = self.client.get(self.url("insights"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="meter"')
        self.assertContains(response, "Pourquoi ce risque ?")
        self.assertContains(response, "Fiche du modèle")

    def test_insight_api_json_structure(self, _):
        self.client.force_login(self.member)
        data = self.client.get(self.url("insight_api", pk=self.budget.pk)).json()
        self.assertTrue(data["ok"] and data["available"])
        self.assertEqual(data["spent_so_far"], "100.000")
        self.assertEqual(len(data["forecast"]["interval_80"]), 2)
        self.assertIn(data["overrun_risk"]["level"], ("low", "medium", "high"))
        self.assertIsInstance(data["overrun_risk"]["factors"], list)

    def test_insight_api_for_past_month(self, _):
        past = make_budget(self.category, date(2026, 9, 1))
        self.client.force_login(self.member)
        data = self.client.get(self.url("insight_api", pk=past.pk)).json()
        self.assertFalse(data["available"])

    def test_outsider_and_foreign_budget_are_refused(self, _):
        self.client.force_login(make_user())
        for url in (self.url("insights"), self.url("insight_api", pk=self.budget.pk)):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        foreign = make_budget(make_category(make_group(admin=self.admin)), OCTOBER)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("insight_api", pk=foreign.pk)).status_code, 404)
