"""Accueil : présentation pour les visiteurs, carnet des groupes avec mon solde pour les membres."""

from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from analytics.services import dashboard_data
from expenses.models import Expense
from groups.models import Group, Membership

User = get_user_model()


class HomeTests(TestCase):
    def test_visitor_sees_presentation(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Les comptes de la coloc")
        self.assertContains(page, reverse("accounts:register"))

    def test_member_sees_groups_and_balances_per_currency(self):
        ala = User.objects.create_user("ala", first_name="Ala")
        sami = User.objects.create_user("sami")
        coloc = Group.objects.create(name="Coloc", currency="TND")
        trip = Group.objects.create(name="Voyage", currency="EUR")
        payer = Membership.objects.create(group=coloc, user=ala, role=Membership.Role.ADMIN)
        Membership.objects.create(group=coloc, user=sami)
        Membership.objects.create(group=trip, user=ala)
        Expense.objects.create(group=coloc, payer=payer, title="Loyer", amount=D("1200")).compute_shares()

        self.client.force_login(ala)
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Bonjour Ala")
        self.assertContains(page, reverse("groups:detail", args=[coloc.pk]))
        self.assertContains(page, reverse("groups:detail", args=[trip.pk]))
        totals = {row["currency"]: row for row in page.context["totals"]}
        self.assertEqual(totals["TND"]["owed"], D("600"))  # jamais additionné avec les EUR
        self.assertEqual(totals["EUR"]["owed"], 0)
        self.assertContains(page, "600,000 TND")
        self.assertNotContains(page, "0,000 EUR")  # ligne vide masquée


class AnalyticsColorTests(TestCase):
    def test_group_specific_categories_get_distinct_colors(self):
        from types import SimpleNamespace
        from datetime import date

        records = [SimpleNamespace(category=name, amount=D(amount), date=date(2026, 10, 1), payer="Ala")
                   for name, amount in [("Loyer", "900"), ("Courses", "200"), ("Logement", "50"), ("Sorties", "30")]]
        order = ["Logement", "Alimentation", "Transport", "Santé", "Shopping"]
        slots = dashboard_data(records, order)["charts"]["category"]["slots"]
        by_name = dict(zip([r.category for r in sorted(records, key=lambda r: -r.amount)], slots))
        self.assertEqual(by_name["Logement"], 0)  # catégorie connue : emplacement fixe
        self.assertEqual(len(set(slots)), len(slots))  # « Loyer », « Courses » et « Sorties » ne se confondent plus
