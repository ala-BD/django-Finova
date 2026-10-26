from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from settlements.models import RepaymentPlan, RiskPrediction, Settlement

from .fixtures import SettlementFixturesMixin

D = Decimal


def url(name, group, **kwargs):
    return reverse(f"settlements:{name}", kwargs={"gid": group.pk, **kwargs})


class AccessControlTests(SettlementFixturesMixin, TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(url("balances", self.group))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_outsider_forbidden_on_every_page(self):
        settlement = self.settlement()
        self.client.force_login(self.imem)
        for name, kwargs in [
            ("balances", {}),
            ("list", {}),
            ("create", {}),
            ("detail", {"sid": settlement.pk}),
            ("plan_create", {"sid": settlement.pk}),
        ]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(url(name, self.group, **kwargs)).status_code, 403)
        response = self.client.post(url("confirm", self.group, sid=settlement.pk))
        self.assertEqual(response.status_code, 403)

    def test_settlement_from_another_group_is_404(self):
        foreign = Settlement.objects.create(
            group=self.other_group, payer=self.m_imem_ala, receiver=self.m_imem, amount=D("5")
        )
        self.client.force_login(self.ala)  # membre des deux groupes
        response = self.client.get(url("detail", self.group, sid=foreign.pk))
        self.assertEqual(response.status_code, 404)


class SettlementViewsTests(SettlementFixturesMixin, TestCase):
    def test_create_valid(self):
        self.client.force_login(self.yahya)
        response = self.client.post(
            url("create", self.group),
            {"receiver": self.m_ala.pk, "amount": "45.500", "note": "virement"},
        )
        settlement = Settlement.objects.get()
        self.assertRedirects(response, url("detail", self.group, sid=settlement.pk))
        self.assertEqual(
            (settlement.payer, settlement.receiver, settlement.amount, settlement.status),
            (self.m_yahya, self.m_ala, D("45.500"), Settlement.Status.PENDING),
        )

    def test_create_invalid_shows_accessible_errors(self):
        self.client.force_login(self.yahya)
        response = self.client.post(url("create", self.group), {"receiver": self.m_yahya.pk, "amount": "0"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Settlement.objects.exists())
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, "Le montant doit être strictement positif.")
        self.assertContains(response, 'aria-invalid="true"')

    def test_receiver_choices_exclude_payer_and_outsiders(self):
        self.client.force_login(self.yahya)
        response = self.client.get(url("create", self.group))
        choices = [m.pk for m in response.context["form"].fields["receiver"].queryset]
        self.assertCountEqual(choices, [self.m_ala.pk, self.m_linda.pk])

    def test_prefill_from_simplified_transfer(self):
        self.client.force_login(self.yahya)
        response = self.client.get(url("create", self.group) + f"?receiver={self.m_ala.pk}&amount=30.000")
        self.assertEqual(response.context["form"].initial["amount"], D("30.000"))

    def test_only_receiver_can_confirm(self):
        settlement = self.settlement()
        for intruder in (self.yahya, self.linda):
            self.client.force_login(intruder)
            response = self.client.post(url("confirm", self.group, sid=settlement.pk))
            self.assertEqual(response.status_code, 403)
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, Settlement.Status.PENDING)

        self.client.force_login(self.ala)
        self.client.post(url("confirm", self.group, sid=settlement.pk))
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, Settlement.Status.CONFIRMED)

    def test_confirm_requires_post(self):
        settlement = self.settlement()
        self.client.force_login(self.ala)
        self.assertEqual(self.client.get(url("confirm", self.group, sid=settlement.pk)).status_code, 405)

    def test_receiver_can_reject(self):
        settlement = self.settlement()
        self.client.force_login(self.ala)
        self.client.post(url("reject", self.group, sid=settlement.pk))
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, Settlement.Status.REJECTED)

    def test_list_filters_by_status(self):
        self.settlement(amount="1")
        self.settlement(amount="2", status=Settlement.Status.CONFIRMED)
        self.client.force_login(self.linda)
        response = self.client.get(url("list", self.group), {"status": "confirmed"})
        self.assertEqual([s.amount for s in response.context["settlements"]], [D("2")])

    def test_balances_page_shows_simplified_transfer(self):
        self.settlement(amount="30", status=Settlement.Status.CONFIRMED)
        self.client.force_login(self.ala)
        response = self.client.get(url("balances", self.group))
        self.assertEqual(response.status_code, 200)
        transfer = response.context["transfers"][0]
        self.assertEqual((transfer["debtor"], transfer["creditor"]), (self.m_ala, self.m_yahya))
        self.assertContains(response, "Rembourser")  # Ala est le débiteur


class PlanViewsTests(SettlementFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.settlement_obj = self.settlement(amount="90.000")
        self.start = timezone.localdate() + timedelta(days=7)

    def create_plan(self, user=None, count=3):
        self.client.force_login(user or self.yahya)
        return self.client.post(
            url("plan_create", self.group, sid=self.settlement_obj.pk),
            {"installments_count": count, "frequency": "monthly", "start_date": self.start.isoformat()},
        )

    def test_payer_creates_plan_with_installments_and_risk(self):
        response = self.create_plan()
        self.assertRedirects(response, url("detail", self.group, sid=self.settlement_obj.pk))
        plan = RepaymentPlan.objects.get()
        self.assertEqual(plan.installments.count(), 3)
        self.assertEqual(plan.total_amount, D("90.000"))
        self.assertEqual(RiskPrediction.objects.filter(plan=plan).count(), 1)

    def test_plan_validation(self):
        self.client.force_login(self.yahya)
        response = self.client.post(
            url("plan_create", self.group, sid=self.settlement_obj.pk),
            {"installments_count": 1, "frequency": "monthly",
             "start_date": (timezone.localdate() - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Il faut au moins 2 échéances.")
        self.assertContains(response, "ne peut pas être dans le passé")
        self.assertFalse(RepaymentPlan.objects.exists())

    def test_third_member_cannot_manage_plan(self):
        response = self.create_plan(user=self.linda)
        self.assertEqual(response.status_code, 403)

    def test_payer_pays_installment_partially_receiver_cannot(self):
        self.create_plan()
        installment = RepaymentPlan.objects.get().installments.first()

        self.client.force_login(self.ala)
        self.assertEqual(
            self.client.get(url("installment_pay", self.group, iid=installment.pk)).status_code, 403
        )

        self.client.force_login(self.yahya)
        self.client.post(url("installment_pay", self.group, iid=installment.pk), {"amount": "10"})
        installment.refresh_from_db()
        self.assertEqual(installment.paid_amount, D("10"))

        response = self.client.post(url("installment_pay", self.group, iid=installment.pk), {"amount": "999"})
        self.assertContains(response, "ne peut pas dépasser le reste dû")

    def test_plan_frozen_after_first_payment(self):
        self.create_plan()
        plan = RepaymentPlan.objects.get()
        plan.installments.first().register_payment(D("1"))
        response = self.client.post(
            url("plan_edit", self.group, sid=self.settlement_obj.pk),
            {"installments_count": 2, "frequency": "weekly", "start_date": self.start.isoformat()},
        )
        self.assertContains(response, "une échéance a déjà été payée")
        self.client.post(url("plan_delete", self.group, sid=self.settlement_obj.pk))
        self.assertTrue(RepaymentPlan.objects.exists())

    def test_edit_regenerates_and_delete_removes(self):
        self.create_plan()
        self.client.post(
            url("plan_edit", self.group, sid=self.settlement_obj.pk),
            {"installments_count": 2, "frequency": "weekly", "start_date": self.start.isoformat()},
        )
        self.assertEqual(RepaymentPlan.objects.get().installments.count(), 2)
        self.client.post(url("plan_delete", self.group, sid=self.settlement_obj.pk))
        self.assertFalse(RepaymentPlan.objects.exists())

    def test_cannot_confirm_while_plan_unpaid(self):
        self.create_plan()
        self.client.force_login(self.ala)
        self.client.post(url("confirm", self.group, sid=self.settlement_obj.pk))
        self.settlement_obj.refresh_from_db()
        self.assertEqual(self.settlement_obj.status, Settlement.Status.PENDING)

        for installment in RepaymentPlan.objects.get().installments.all():
            installment.register_payment(installment.amount)
        self.client.post(url("confirm", self.group, sid=self.settlement_obj.pk))
        self.settlement_obj.refresh_from_db()
        self.assertEqual(self.settlement_obj.status, Settlement.Status.CONFIRMED)

    def test_detail_page_renders_plan_and_risk(self):
        self.create_plan()
        response = self.client.get(url("detail", self.group, sid=self.settlement_obj.pk))
        self.assertContains(response, "Risque de retard")
        self.assertContains(response, "jeu de démonstration généré")


class ReminderViewsTests(SettlementFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.settlement_obj = self.settlement(amount="75.000")

    def test_generate_with_mocked_llm(self):
        self.client.force_login(self.ala)
        with patch("settlements.services.call_llm", return_value="Salut Yahya, il reste 75 TND. Merci !"):
            response = self.client.post(url("reminder", self.group, sid=self.settlement_obj.pk), {"tone": "friendly"})
        data = response.json()
        self.assertEqual(data["source"], "llm")
        self.assertIn("75", data["message"])

    def test_only_receiver_can_generate(self):
        self.client.force_login(self.yahya)
        response = self.client.post(url("reminder", self.group, sid=self.settlement_obj.pk), {"tone": "firm"})
        self.assertEqual(response.status_code, 403)

    def test_invalid_tone(self):
        self.client.force_login(self.ala)
        response = self.client.post(url("reminder", self.group, sid=self.settlement_obj.pk), {"tone": "rude"})
        self.assertEqual(response.status_code, 400)

    def test_send_warns_when_notifications_unavailable(self):
        self.client.force_login(self.ala)
        # Panne simulée explicitement : le test reste valable une fois M6 fusionné
        with patch("automation.services.notify", side_effect=RuntimeError("M6 indisponible")):
            response = self.client.post(
                url("reminder_send", self.group, sid=self.settlement_obj.pk),
                {"message": "Merci de rembourser."},
                follow=True,
            )
        self.assertContains(response, "notifications (M6) ne sont pas encore disponibles")

    def test_send_uses_notify(self):
        self.client.force_login(self.ala)
        with patch("automation.services.notify") as notify:
            self.client.post(
                url("reminder_send", self.group, sid=self.settlement_obj.pk),
                {"message": "Merci de rembourser."},
            )
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], self.yahya)
