from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from budgets import services
from budgets.models import Budget, BudgetForecast
from core.ai import LLMError
from core.models import AIRequestLog

from .factories import add_member, make_budget, make_category, make_expense, make_group

SEPTEMBER = date(2026, 9, 1)
OCTOBER = date(2026, 10, 1)
NOTIFY = "automation.services.notify"


class MonthHelperTests(TestCase):
    def test_parse_month(self):
        self.assertEqual(services.parse_month("2026-10"), OCTOBER)
        self.assertIsNone(services.parse_month("octobre"))
        self.assertIsNone(services.parse_month("2026-13"))
        self.assertIsNone(services.parse_month(None))

    def test_add_months_across_years(self):
        self.assertEqual(services.add_months(date(2026, 11, 1), 3), date(2027, 2, 1))
        self.assertEqual(services.add_months(date(2026, 1, 1), -1), date(2025, 12, 1))

    def test_month_bounds(self):
        self.assertEqual(services.month_bounds(date(2028, 2, 10)), (date(2028, 2, 1), date(2028, 2, 29)))


class ConsumptionTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.food = make_category(self.group, "Alimentation")
        self.fun = make_category(self.group, "Loisirs")

    def test_consumed_only_counts_category_month_and_group(self):
        make_expense(self.food, "100.500", "2026-10-01")
        make_expense(self.food, "49.500", "2026-10-31")
        make_expense(self.food, "999", "2026-09-30")  # autre mois
        make_expense(self.fun, "999", "2026-10-15")  # autre catégorie
        other_group = make_group()
        make_expense(make_category(other_group, "Alimentation"), "999", "2026-10-15")
        self.assertEqual(services.consumed_amount(self.group, self.food, OCTOBER), Decimal("150.000"))

    def test_status_without_budget(self):
        make_expense(self.food, "20", "2026-10-02")
        status = services.get_budget_status(self.group, self.food, OCTOBER)
        self.assertIsNone(status["percent"])
        self.assertEqual(status["level"], "none")
        self.assertEqual(status["consumed"], Decimal("20"))

    def test_status_levels(self):
        make_budget(self.food, OCTOBER, limit="200")
        make_expense(self.food, "150", "2026-10-02")
        status = services.get_budget_status(self.group, self.food, OCTOBER)
        self.assertEqual(status["percent"], Decimal("75.0"))
        self.assertEqual(status["level"], "ok")
        self.assertEqual(status["remaining"], Decimal("50"))

        make_expense(self.food, "10", "2026-10-03")
        self.assertEqual(services.get_budget_status(self.group, self.food, OCTOBER)["level"], "warning")
        make_expense(self.food, "40", "2026-10-04")
        status = services.get_budget_status(self.group, self.food, OCTOBER)
        self.assertEqual(status["percent"], Decimal("100.0"))
        self.assertEqual(status["level"], "over")


class CarryOverTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.category = make_category(self.group)

    def test_remainder_added_to_next_month_when_enabled(self):
        make_budget(self.category, SEPTEMBER, limit="200", carry_over=True)
        make_expense(self.category, "150", "2026-09-10")
        october = make_budget(self.category, OCTOBER, limit="200")
        self.assertEqual(services.carried_over_amount(october), Decimal("50"))
        self.assertEqual(services.effective_limit(october), Decimal("250"))

    def test_no_carry_over_when_disabled(self):
        make_budget(self.category, SEPTEMBER, limit="200", carry_over=False)
        october = make_budget(self.category, OCTOBER, limit="200")
        self.assertEqual(services.effective_limit(october), Decimal("200"))

    def test_overspending_is_not_carried_as_negative(self):
        make_budget(self.category, SEPTEMBER, limit="100", carry_over=True)
        make_expense(self.category, "180", "2026-09-10")
        october = make_budget(self.category, OCTOBER, limit="200")
        self.assertEqual(services.effective_limit(october), Decimal("200"))

    def test_carry_over_chains_over_several_months(self):
        make_budget(self.category, date(2026, 8, 1), limit="100", carry_over=True)  # reste 100
        make_budget(self.category, SEPTEMBER, limit="100", carry_over=True)  # 200 dispo, reste 200
        october = make_budget(self.category, OCTOBER, limit="100")
        self.assertEqual(services.effective_limit(october), Decimal("300"))


class AlertTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.member = add_member(self.group)
        self.category = make_category(self.group, "Restaurants")
        self.budget = make_budget(self.category, OCTOBER, limit="100")

    def test_80_percent_alert_sent_once_to_every_member(self):
        make_expense(self.category, "85", "2026-10-02")
        with mock.patch(NOTIFY) as notify:
            self.assertEqual(services.check_budget_alerts(self.budget), [80])
            self.assertEqual(services.check_budget_alerts(self.budget), [])
        self.assertEqual(notify.call_count, 2)  # admin + membre, une seule fois
        recipients = {call.args[0] for call in notify.call_args_list}
        self.assertEqual(recipients, {self.group.created_by, self.member.user})
        self.assertEqual(notify.call_args.args[1], "budget_alert_80")
        self.budget.refresh_from_db()
        self.assertTrue(self.budget.alert_80_sent)
        self.assertFalse(self.budget.alert_100_sent)

    def test_jump_over_100_sends_both_alerts(self):
        make_expense(self.category, "120", "2026-10-02")
        with mock.patch(NOTIFY):
            self.assertEqual(services.check_budget_alerts(self.budget), [80, 100])

    def test_alert_triggered_by_expense_signal(self):
        with mock.patch(NOTIFY) as notify:
            make_expense(self.category, "50", "2026-10-02")
            notify.assert_not_called()
            make_expense(self.category, "40", "2026-10-03")
        self.assertTrue(notify.called)
        self.budget.refresh_from_db()
        self.assertTrue(self.budget.alert_80_sent)

    def test_flag_reverted_when_notify_unavailable(self):
        make_expense(self.category, "90", "2026-10-02")
        with mock.patch(NOTIFY, side_effect=NotImplementedError):
            self.assertEqual(services.check_budget_alerts(self.budget), [])
        self.budget.refresh_from_db()
        self.assertFalse(self.budget.alert_80_sent)

    def test_raising_limit_rearms_alert(self):
        make_expense(self.category, "90", "2026-10-02")
        with mock.patch(NOTIFY):
            services.check_budget_alerts(self.budget)
        Budget.objects.filter(pk=self.budget.pk).update(limit_amount=Decimal("200"))
        self.budget.refresh_from_db()
        services.reset_alerts_below_threshold(self.budget)
        self.budget.refresh_from_db()
        self.assertFalse(self.budget.alert_80_sent)

    def test_expense_without_category_or_budget_is_ignored(self):
        other = make_category(self.group)
        with mock.patch(NOTIFY) as notify:
            make_expense(other, "500", "2026-10-02")
        notify.assert_not_called()


class SimulatorTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.category = make_category(self.group, "Restaurants")

    def test_example_from_report_300_tnd_minus_20_percent(self):
        for month in ("2026-07-10", "2026-08-10", "2026-09-10"):
            make_expense(self.category, "300", month)
        result = services.simulate_savings(self.group, self.category, 20, 360, today=date(2026, 10, 5))
        self.assertEqual(result["monthly_average"], Decimal("300"))
        self.assertEqual(result["monthly_saving"], Decimal("60"))
        self.assertEqual(result["new_monthly_spend"], Decimal("240"))
        self.assertEqual(result["months_needed"], 6)
        self.assertEqual(result["reached_on"], date(2027, 4, 1))

    def test_months_rounded_up(self):
        make_expense(self.category, "300", "2026-09-10")
        result = services.simulate_savings(self.group, self.category, 20, 370, today=date(2026, 10, 5))
        self.assertEqual(result["months_needed"], 7)

    def test_no_history_gives_no_deadline(self):
        result = services.simulate_savings(self.group, self.category, 20, 360, today=date(2026, 10, 5))
        self.assertIsNone(result["months_needed"])
        self.assertEqual(result["months_of_history"], 0)

    def test_history_ignores_months_before_first_expense(self):
        make_expense(self.category, "200", "2026-09-10")
        history = services.monthly_history(self.group, self.category, OCTOBER)
        self.assertEqual([row["month"] for row in history], [SEPTEMBER])

    def test_current_month_excluded_from_history(self):
        make_expense(self.category, "100", "2026-09-10")
        make_expense(self.category, "900", "2026-10-02")
        history = services.monthly_history(self.group, self.category, OCTOBER)
        self.assertEqual([row["total"] for row in history], [Decimal("100")])


class ForecastServiceTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.category = make_category(self.group, "Factures")
        self.budget = make_budget(self.category, OCTOBER, limit="150")
        for month in (7, 8, 9):
            make_expense(self.category, "100", date(2026, month, 8))
            make_expense(self.category, "50", date(2026, month, 20))

    def test_profile_forecast_and_single_row_per_day(self):
        make_expense(self.category, "100", "2026-10-08")
        today = date(2026, 10, 10)
        result = services.forecast_budget(self.budget, today=today)
        # déjà dépensé 100 + reste habituel après le 10 (50) = 150
        self.assertEqual(result["predicted_total"], Decimal("150.000"))
        self.assertEqual(result["baseline_total"], Decimal("150.000"))
        self.assertFalse(result["likely_overrun"])
        services.forecast_budget(self.budget, today=today)
        self.assertEqual(BudgetForecast.objects.filter(budget=self.budget).count(), 1)
        self.assertTrue(AIRequestLog.objects.filter(module="budgets", feature="forecast", success=True).exists())

    def test_overrun_detected(self):
        make_expense(self.category, "140", "2026-10-08")
        result = services.forecast_budget(self.budget, today=date(2026, 10, 10))
        self.assertTrue(result["likely_overrun"])
        self.assertEqual(result["overrun_amount"], Decimal("40.000"))

    def test_no_forecast_outside_current_month(self):
        self.assertIsNone(services.forecast_budget(self.budget, today=date(2026, 11, 3)))


class RecommendationTests(TestCase):
    def setUp(self):
        self.group = make_group()
        self.category = make_category(self.group, "Alimentation")
        for month, amount in ((7, "280"), (8, "300"), (9, "320")):
            make_expense(self.category, amount, date(2026, month, 5))

    def test_suggested_limit_is_average_plus_margin(self):
        with mock.patch("core.ai.call_llm", side_effect=LLMError("pas de clé")):
            result = services.recommend_budget(self.group, self.category, OCTOBER)
        self.assertEqual(result["suggested_limit"], Decimal("330"))
        self.assertEqual(result["average"], Decimal("300.000"))
        self.assertEqual(result["source"], "fallback")
        self.assertIn("330", result["explanation"])

    def test_llm_explanation_used_when_coherent(self):
        text = "Une limite de 330 TND couvre votre moyenne de 300 TND avec une marge de 10 %."
        with mock.patch("core.ai.call_llm", return_value=text) as call:
            result = services.recommend_budget(self.group, self.category, OCTOBER)
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["explanation"], text)
        self.assertEqual(call.call_args.kwargs["module"], "budgets")

    def test_llm_answer_without_the_figure_is_rejected(self):
        with mock.patch("core.ai.call_llm", return_value="Dépensez moins, c'est tout."):
            result = services.recommend_budget(self.group, self.category, OCTOBER)
        self.assertEqual(result["source"], "fallback")

    def test_no_history(self):
        empty = make_category(self.group)
        result = services.recommend_budget(self.group, empty, OCTOBER)
        self.assertIsNone(result["suggested_limit"])
        self.assertEqual(result["source"], "none")
