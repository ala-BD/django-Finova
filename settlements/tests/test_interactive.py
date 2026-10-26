"""IA interactive : simulateur de risque (calcul dans le navigateur) et graphe des dettes."""

import math
import random
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from groups.models import Membership
from settlements import risk, services
from settlements.models import RepaymentPlan, Settlement

from .fixtures import SettlementFixturesMixin, make_user

D = Decimal


def url(name, group, **kwargs):
    return reverse(f"settlements:{name}", kwargs={"gid": group.pk, **kwargs})


class ModelParamsParityTests(TestCase):
    """La formule exécutée par static/js/m3_simulator.js doit égaler scikit-learn."""

    def browser_formula(self, params, features):
        z = params["intercept"] + sum(
            params["coef"][i] * (features[name] - params["mean"][i]) / params["scale"][i]
            for i, name in enumerate(params["features"])
        )
        return 1 / (1 + math.exp(-z))

    def test_same_probability_as_sklearn(self):
        params = risk.model_params()
        pipeline = risk.load_model()["pipeline"]
        rng = random.Random(3)
        for _ in range(50):
            amount = rng.uniform(10, 3000)
            features = {
                "amount": amount, "installments_count": rng.randint(2, 12),
                "past_late_count": rng.randint(0, 8), "avg_confirmation_delay_days": rng.uniform(0, 15),
                "amount_weight_ratio": amount / rng.choice([1, 2, 3]), "days_since_creation": rng.randint(0, 120),
            }
            expected = pipeline.predict_proba([[features[n] for n in risk.FEATURES]])[0][1]
            self.assertAlmostEqual(self.browser_formula(params, features), expected, places=5)

    def test_params_are_json_friendly(self):
        params = risk.model_params()
        self.assertEqual(params["features"], risk.FEATURES)
        self.assertEqual(len(params["coef"]), len(risk.FEATURES))
        self.assertTrue(all(isinstance(v, float) for v in params["mean"] + params["scale"] + params["coef"]))


class DebtGraphTests(SettlementFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.zahra = make_user("zahra", first_name="Zahra")
        self.m_zahra = Membership.objects.create(group=self.group, user=self.zahra)
        # Cycle Ala → Yahya → Linda → Zahra → Ala (sens « doit à »), plus une dette réciproque
        for payer, receiver, amount in [
            (self.m_yahya, self.m_ala, "40"),   # Ala doit 40 à Yahya
            (self.m_linda, self.m_yahya, "30"),  # Yahya doit 30 à Linda
            (self.m_zahra, self.m_linda, "20"),  # Linda doit 20 à Zahra
            (self.m_ala, self.m_zahra, "10"),   # Zahra doit 10 à Ala
            (self.m_ala, self.m_yahya, "15"),   # Yahya doit 15 à Ala : compense en partie Ala → Yahya
        ]:
            Settlement.objects.create(group=self.group, payer=payer, receiver=receiver,
                                      amount=D(amount), status=Settlement.Status.CONFIRMED)
        Settlement.objects.create(group=self.group, payer=self.m_linda, receiver=self.m_ala, amount=D("999"))

    def test_pairwise_debts_net_reciprocal_and_ignore_pending(self):
        debts = services.pairwise_debts(self.group)
        self.assertEqual(debts[(self.ala.pk, self.yahya.pk)], D("25"))  # 40 − 15
        self.assertNotIn((self.yahya.pk, self.ala.pk), debts)
        self.assertNotIn((self.ala.pk, self.linda.pk), debts)  # remboursement en attente ignoré
        self.assertEqual(len(debts), 4)

    def test_edges_are_consistent_with_balances(self):
        balances = services.compute_balances(self.group)
        net = {user_id: D("0") for user_id in balances}
        for (debtor, creditor), amount in services.pairwise_debts(self.group).items():
            net[debtor] -= amount
            net[creditor] += amount
        self.assertEqual(net, balances)

    def test_graph_before_and_after(self):
        graph = services.debt_graph(self.group)
        self.assertEqual(len(graph["nodes"]), 4)
        self.assertEqual(len(graph["before"]), 4)
        self.assertLessEqual(len(graph["after"]), len(graph["nodes"]) - 1)
        self.assertLess(len(graph["after"]), len(graph["before"]))
        self.assertEqual(
            sum(e["amount"] for e in graph["before"] if e["from"] == self.ala.pk)
            - sum(e["amount"] for e in graph["before"] if e["to"] == self.ala.pk),
            sum(e["amount"] for e in graph["after"] if e["from"] == self.ala.pk)
            - sum(e["amount"] for e in graph["after"] if e["to"] == self.ala.pk),
        )

    def test_balances_page_embeds_graph(self):
        self.client.force_login(self.linda)
        response = self.client.get(url("balances", self.group))
        self.assertContains(response, 'id="graph-data"')
        self.assertContains(response, "m3_debt_graph.js")
        self.assertEqual(len(response.context["graph_data"]["before"]), 4)


class SimulatorViewTests(SettlementFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.settlement_obj = self.settlement(amount="240.000")

    def get(self, user):
        self.client.force_login(user)
        return self.client.get(url("simulate", self.group, sid=self.settlement_obj.pk))

    def test_simulator_without_plan_starts_from_real_features(self):
        response = self.get(self.yahya)
        self.assertEqual(response.status_code, 200)
        simulator = response.context["simulator"]
        self.assertEqual(list(simulator["initial"]), risk.FEATURES)
        self.assertEqual(simulator["initial"]["installments_count"], 3)
        self.assertEqual(simulator["weight"], 2.0)
        self.assertEqual(simulator["apply_url"], url("plan_create", self.group, sid=self.settlement_obj.pk))
        self.assertContains(response, 'id="simulator-data"')

    def test_simulator_with_plan_compares_to_saved_prediction(self):
        plan = RepaymentPlan(settlement=self.settlement_obj, installments_count=6,
                             start_date=timezone.localdate() + timedelta(days=7))
        services.save_plan(plan)
        simulator = self.get(self.yahya).context["simulator"]
        self.assertEqual(simulator["initial"]["installments_count"], 6)
        self.assertEqual(simulator["saved_percent"], plan.latest_prediction().percent)
        self.assertEqual(simulator["apply_url"], url("plan_edit", self.group, sid=self.settlement_obj.pk))

    def test_third_member_can_simulate_but_not_apply(self):
        response = self.get(self.linda)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["simulator"]["apply_url"])
        self.assertNotContains(response, "Appliquer cet échéancier")

    def test_outsider_forbidden(self):
        self.assertEqual(self.get(self.imem).status_code, 403)

    def test_missing_model_redirects_with_message(self):
        with patch.object(risk, "load_model", side_effect=risk.RiskModelUnavailable("absent")):
            response = self.get(self.yahya)
        self.assertRedirects(response, url("detail", self.group, sid=self.settlement_obj.pk))

    def test_apply_prefills_plan_form(self):
        self.client.force_login(self.yahya)
        response = self.client.get(
            url("plan_create", self.group, sid=self.settlement_obj.pk) + "?installments_count=5&frequency=weekly"
        )
        form = response.context["form"]
        self.assertEqual((form.initial["installments_count"], form.initial["frequency"]), ("5", "weekly"))
