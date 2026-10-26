from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from settlements.models import Installment, RepaymentPlan, Settlement

from .fixtures import SettlementFixturesMixin

D = Decimal


class SettlementModelTests(SettlementFixturesMixin, TestCase):
    def test_payer_must_differ_from_receiver(self):
        settlement = Settlement(group=self.group, payer=self.m_ala, receiver=self.m_ala, amount=D("5"))
        with self.assertRaises(ValidationError):
            settlement.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            settlement.save()

    def test_both_parties_must_be_members_of_the_group(self):
        settlement = Settlement(group=self.group, payer=self.m_yahya, receiver=self.m_imem, amount=D("5"))
        with self.assertRaises(ValidationError) as ctx:
            settlement.full_clean()
        self.assertIn("receiver", ctx.exception.message_dict)

    def test_amount_strictly_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.settlement(amount="0")

    def test_status_transitions(self):
        settlement = self.settlement()
        settlement.reject()
        self.assertEqual(settlement.status, Settlement.Status.REJECTED)
        with self.assertRaises(ValidationError):
            settlement.confirm()


class RepaymentPlanTests(SettlementFixturesMixin, TestCase):
    def make_plan(self, amount="100.000", count=3, frequency="monthly", start=date(2026, 1, 31)):
        settlement = self.settlement(amount=amount)
        plan = RepaymentPlan.objects.create(
            settlement=settlement,
            total_amount=D(amount),
            installments_count=count,
            frequency=frequency,
            start_date=start,
        )
        plan.generate_installments()
        return plan

    def test_amounts_sum_exactly_last_absorbs_rounding(self):
        plan = self.make_plan()
        amounts = list(plan.installments.values_list("amount", flat=True))
        self.assertEqual(amounts, [D("33.333"), D("33.333"), D("33.334")])
        self.assertEqual(sum(amounts), plan.total_amount)

    def test_monthly_due_dates_clamped_to_month_end(self):
        plan = self.make_plan()
        self.assertEqual(
            list(plan.installments.values_list("due_date", flat=True)),
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
        )

    def test_weekly_and_biweekly_due_dates(self):
        weekly = self.make_plan(frequency="weekly", start=date(2026, 11, 2))
        self.assertEqual(weekly.installments.last().due_date, date(2026, 11, 16))
        biweekly = self.make_plan(frequency="biweekly", start=date(2026, 11, 2))
        self.assertEqual(biweekly.installments.last().due_date, date(2026, 11, 30))

    def test_partial_payment_then_full_payment(self):
        plan = self.make_plan(amount="60.000", count=2)
        first = plan.installments.first()
        first.register_payment(D("10"), today=date(2026, 1, 31))
        self.assertEqual((first.status, first.paid_amount), (Installment.Status.PARTIAL, D("10")))
        first.register_payment(D("20"), today=date(2026, 2, 2))
        self.assertEqual(first.status, Installment.Status.PAID)
        self.assertEqual(first.paid_at, date(2026, 2, 2))
        self.assertEqual(plan.remaining_amount, D("30"))

    def test_never_pay_more_than_due(self):
        installment = self.make_plan(amount="60.000", count=2).installments.first()
        with self.assertRaises(ValidationError):
            installment.register_payment(D("30.001"))
        with self.assertRaises(ValidationError):
            installment.register_payment(D("0"))

    def test_plan_completed_when_all_installments_paid(self):
        plan = self.make_plan(amount="60.000", count=2)
        for installment in plan.installments.all():
            installment.register_payment(installment.amount)
        plan.refresh_from_db()
        self.assertEqual(plan.status, RepaymentPlan.Status.COMPLETED)

    def test_late_means_more_than_three_days(self):
        installment = self.make_plan().installments.first()
        due = installment.due_date
        self.assertFalse(installment.is_late(today=due + timedelta(days=3)))
        self.assertTrue(installment.is_late(today=due + timedelta(days=4)))
        installment.register_payment(installment.amount, today=due + timedelta(days=1))
        self.assertFalse(installment.is_late(today=due + timedelta(days=30)))

    def test_at_least_two_installments(self):
        settlement = self.settlement()
        plan = RepaymentPlan(settlement=settlement, total_amount=D("60"), installments_count=1,
                             start_date=date(2026, 12, 1))
        with self.assertRaises(ValidationError):
            plan.full_clean()
