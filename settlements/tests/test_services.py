import random
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from groups.services import can_leave, get_member_balance
from settlements import services
from settlements.models import Settlement

from .fixtures import SettlementFixturesMixin

D = Decimal


class SimplifyDebtsTests(TestCase):
    def test_report_example(self):
        # A +50, B −20, C −30 ⇒ C → A 30 et B → A 20
        transfers = services.simplify_debts({"A": D("50"), "B": D("-20"), "C": D("-30")})
        self.assertEqual(transfers, [("C", "A", D("30")), ("B", "A", D("20"))])

    def test_empty_and_balanced(self):
        self.assertEqual(services.simplify_debts({}), [])
        self.assertEqual(services.simplify_debts({"A": D("0"), "B": D("0")}), [])

    def test_property_at_most_n_minus_one_transfers_that_settle_everything(self):
        rng = random.Random(7)
        for _ in range(200):
            n = rng.randint(2, 9)
            values = [D(rng.randint(-50_000, 50_000)) / 1000 for _ in range(n - 1)]
            values.append(-sum(values))  # Σ des soldes = 0
            balances = {f"m{i}": value for i, value in enumerate(values)}

            transfers = services.simplify_debts(balances)

            self.assertLessEqual(len(transfers), n - 1)
            remaining = dict(balances)
            for debtor, creditor, amount in transfers:
                self.assertGreater(amount, 0)
                remaining[debtor] += amount
                remaining[creditor] -= amount
            self.assertTrue(all(value == 0 for value in remaining.values()), remaining)


class NetBalancesTests(TestCase):
    def test_known_scenario(self):
        # Ala paie 90, partagés en trois ; puis Yahya rembourse 30 à Ala
        balances = services.net_balances(
            paid={1: D("90")},
            owed={1: D("30"), 2: D("30"), 3: D("30")},
            transfers=[(2, 1, D("30"))],
        )
        self.assertEqual(balances, {1: D("30"), 2: D("0"), 3: D("-30")})
        self.assertEqual(sum(balances.values()), 0)


class ComputeBalancesTests(SettlementFixturesMixin, TestCase):
    def test_every_active_member_present_without_expenses(self):
        self.assertEqual(
            services.compute_balances(self.group),
            {self.ala.pk: 0, self.yahya.pk: 0, self.linda.pk: 0},
        )

    def test_only_confirmed_settlements_count(self):
        self.settlement(amount="10")  # en attente
        self.settlement(amount="20", status=Settlement.Status.REJECTED)
        self.settlement(amount="30", status=Settlement.Status.CONFIRMED)
        balances = services.compute_balances(self.group)
        self.assertEqual(balances[self.yahya.pk], D("30"))
        self.assertEqual(balances[self.ala.pk], D("-30"))

    def test_combines_m2_shares_and_settlements(self):
        flows = ({self.ala.pk: D("90")}, {self.ala.pk: D("30"), self.yahya.pk: D("30"), self.linda.pk: D("30")})
        self.settlement(amount="30", status=Settlement.Status.CONFIRMED)
        with patch.object(services, "expense_flows", return_value=flows):
            balances = services.compute_balances(self.group)
            overview = services.balance_overview(self.group)
        self.assertEqual(balances, {self.ala.pk: D("30"), self.yahya.pk: D("0"), self.linda.pk: D("-30")})
        self.assertEqual(len(overview["transfers"]), 1)
        transfer = overview["transfers"][0]
        self.assertEqual((transfer["debtor"], transfer["creditor"]), (self.m_linda, self.m_ala))

    def test_m1_contract_leave_blocked_with_non_zero_balance(self):
        self.settlement(amount="30", status=Settlement.Status.CONFIRMED)
        self.assertEqual(get_member_balance(self.yahya, self.group), D("30"))
        allowed, _ = can_leave(self.yahya, self.group)
        self.assertFalse(allowed)
        self.assertTrue(can_leave(self.linda, self.group)[0])

    def test_expense_flows_none_until_m2_models_exist(self):
        # Simule l'absence des modèles M2 : le test reste valable une fois M2 fusionné
        with patch("settlements.services.apps.get_model", side_effect=LookupError):
            self.assertIsNone(services.expense_flows(self.group))
            self.assertFalse(services.balance_overview(self.group)["expenses_integrated"])


class SettlementWorkflowTests(SettlementFixturesMixin, TestCase):
    def test_create_validates_members_and_amount(self):
        with self.assertRaises(ValidationError):
            services.create_settlement(self.group, self.m_yahya, self.m_yahya, D("10"))
        with self.assertRaises(ValidationError):
            services.create_settlement(self.group, self.m_yahya, self.m_imem, D("10"))
        with self.assertRaises(ValidationError):
            services.create_settlement(self.group, self.m_yahya, self.m_ala, D("0"))
        settlement = services.create_settlement(self.group, self.m_yahya, self.m_ala, D("10"))
        self.assertEqual(settlement.status, Settlement.Status.PENDING)

    def test_only_receiver_confirms_and_it_is_irreversible(self):
        settlement = self.settlement()
        with self.assertRaises(PermissionDenied):
            services.confirm_settlement(settlement, self.yahya)
        services.confirm_settlement(settlement, self.ala)
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, Settlement.Status.CONFIRMED)
        self.assertIsNotNone(settlement.confirmed_at)
        with self.assertRaises(ValidationError):
            services.reject_settlement(settlement, self.ala)

    def test_notification_failure_never_blocks(self):
        with patch("automation.services.notify", side_effect=RuntimeError("M6 indisponible")):
            settlement = services.create_settlement(self.group, self.m_yahya, self.m_ala, D("5"))
            services.confirm_settlement(settlement, self.ala)
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, Settlement.Status.CONFIRMED)

    def test_notifies_through_m6_contract(self):
        with patch("automation.services.notify") as notify:
            settlement = services.create_settlement(self.group, self.m_yahya, self.m_ala, D("5"))
            services.confirm_settlement(settlement, self.ala)
        recipients = [call.args[0] for call in notify.call_args_list]
        self.assertEqual(recipients, [self.ala, self.yahya])
        self.assertEqual(notify.call_args_list[0].args[3], f"/groups/{self.group.pk}/settlements/{settlement.pk}/")
