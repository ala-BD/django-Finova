"""Partie IA (fonctions pures) : prévision sur séries synthétiques à tendance connue."""

from decimal import Decimal

from django.test import SimpleTestCase

from budgets.forecasting import daily_rate, forecast_month_total, naive_forecast, recommend_limit


class ForecastTests(SimpleTestCase):
    def test_no_data_at_all(self):
        result = forecast_month_total([], 30)
        self.assertEqual(result.predicted_total, Decimal("0"))
        self.assertIsNone(result.baseline_total)

    def test_regression_recovers_known_linear_trend(self):
        # 10 TND par jour pendant 15 jours, sans historique → 300 sur 30 jours.
        result = forecast_month_total([10] * 15, 30)
        self.assertEqual(result.method, "rythme actuel")
        self.assertEqual(result.predicted_total, Decimal("300.000"))
        self.assertAlmostEqual(daily_rate([10] * 15), 10.0, places=6)

    def test_profile_respects_one_off_expenses(self):
        # Loyer de 1000 le 1er : l'extrapolation linéaire prévoirait ~3000,
        # le profil historique sait qu'il ne reste rien à payer.
        daily = [1000] + [0] * 9
        history = [1000, 1000, 1000]
        cumulative = [1000, 1000, 1000]
        result = forecast_month_total(daily, 30, history, cumulative)
        self.assertEqual(result.method, "profil historique")
        self.assertEqual(result.predicted_total, Decimal("1000.000"))

    def test_profile_beats_naive_on_unusual_month(self):
        # Historique stable à 300 (10 par jour) ; ce mois-ci 20 par jour.
        history = [300, 300, 300]
        cumulative = [100, 100, 100]  # cumul habituel au jour 10
        result = forecast_month_total([20] * 10, 30, history, cumulative)
        actual = 20 * 10 + 10 * 20  # 200 déjà dépensés + reste au rythme habituel
        self.assertEqual(result.predicted_total, Decimal(actual))
        self.assertEqual(result.baseline_total, Decimal("300.000"))

    def test_one_month_history_blends_both_estimates(self):
        result = forecast_month_total([10] * 10, 30, [300], [100])
        self.assertEqual(result.method, "rythme + historique")
        self.assertEqual(result.predicted_total, Decimal("300.000"))

    def test_month_finished_returns_actual_total(self):
        result = forecast_month_total([5] * 30, 30, [100])
        self.assertEqual(result.predicted_total, Decimal("150.000"))
        self.assertEqual(result.method, "mois terminé")

    def test_naive_forecast(self):
        self.assertEqual(naive_forecast([100, 200, 300]), Decimal("200.000"))
        self.assertIsNone(naive_forecast([]))


class RecommendLimitTests(SimpleTestCase):
    def test_average_of_last_three_months_plus_margin_rounded_up(self):
        self.assertEqual(recommend_limit([1000, 280, 300, 320]), Decimal("330"))
        self.assertEqual(recommend_limit([301]), Decimal("340"))

    def test_no_history(self):
        self.assertIsNone(recommend_limit([]))
        self.assertIsNone(recommend_limit([0, 0]))
