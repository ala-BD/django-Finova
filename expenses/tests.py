from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Category
from groups.models import Group, Membership
from .models import Expense, ExpenseItem, ExpenseShare, ItemClaim
from .services import get_group_expenses

User = get_user_model()


class ExpenseModelAndServiceTests(TestCase):
    """Tests unitaires sur les modèles M2 et les algorithmes de répartition (Yahya)."""

    def setUp(self):
        self.user1 = User.objects.create_user(username="alice", email="alice@test.com", password="PassWord123!")
        self.user2 = User.objects.create_user(username="bob", email="bob@test.com", password="PassWord123!")
        self.user3 = User.objects.create_user(username="charlie", email="charlie@test.com", password="PassWord123!")

        self.group = Group.objects.create(name="Coloc Djerba", currency="TND", created_by=self.user1)
        self.m_alice = Membership.objects.create(user=self.user1, group=self.group, role="admin", weight=Decimal("1.00"))
        self.m_bob = Membership.objects.create(user=self.user2, group=self.group, role="member", weight=Decimal("2.00"))
        self.m_charlie = Membership.objects.create(user=self.user3, group=self.group, role="member", weight=Decimal("3.00"))

        self.category = Category.objects.create(group=self.group, name="Restaurant", color="#e11d48")

    def test_expense_validation_amount_and_date(self):
        """Un montant <= 0 ou une date future doivent lever une ValidationError."""
        exp_invalid_amount = Expense(
            group=self.group,
            payer=self.m_alice,
            title="Négatif",
            amount=Decimal("-10.000"),
        )
        with self.assertRaises(ValidationError):
            exp_invalid_amount.clean()

        future_date = timezone.localdate() + timezone.timedelta(days=2)
        exp_future_date = Expense(
            group=self.group,
            payer=self.m_alice,
            title="Futur",
            amount=Decimal("50.000"),
            expense_date=future_date,
        )
        with self.assertRaises(ValidationError):
            exp_future_date.clean()

    def test_expense_item_total_property(self):
        """Vérifie le calcul unitaire total d'une ligne de ticket."""
        expense = Expense.objects.create(
            group=self.group,
            payer=self.m_alice,
            title="Courses",
            amount=Decimal("30.000"),
        )
        item = ExpenseItem.objects.create(
            expense=expense,
            label="Eau minérale",
            unit_price=Decimal("1.500"),
            quantity=4,
        )
        self.assertEqual(item.total, Decimal("6.000"))

    def test_compute_shares_equal_rule(self):
        """Répartition égale : 100 TND partagé entre 3 membres = somme exacte 100 TND."""
        expense = Expense.objects.create(
            group=self.group,
            payer=self.m_alice,
            title="Courses communes",
            amount=Decimal("100.000"),
            split_rule=Expense.SplitRule.EQUAL,
        )
        expense.compute_shares()

        shares = ExpenseShare.objects.filter(expense=expense)
        self.assertEqual(shares.count(), 3)
        self.assertEqual(sum(s.amount_due for s in shares), Decimal("100.000"))

    def test_compute_shares_by_weight_rule(self):
        """Répartition par poids : 90 TND entre poids 1, 2 et 3 => 15, 30, 45 TND."""
        expense = Expense.objects.create(
            group=self.group,
            payer=self.m_alice,
            title="Dîner d'équipe",
            amount=Decimal("90.000"),
            split_rule=Expense.SplitRule.BY_WEIGHT,
        )
        expense.compute_shares()

        share_alice = ExpenseShare.objects.get(expense=expense, member=self.m_alice)
        share_bob = ExpenseShare.objects.get(expense=expense, member=self.m_bob)
        share_charlie = ExpenseShare.objects.get(expense=expense, member=self.m_charlie)

        self.assertEqual(share_alice.amount_due, Decimal("15.000"))
        self.assertEqual(share_bob.amount_due, Decimal("30.000"))
        self.assertEqual(share_charlie.amount_due, Decimal("45.000"))
        self.assertEqual(share_alice.amount_due + share_bob.amount_due + share_charlie.amount_due, Decimal("90.000"))

    def test_compute_shares_by_item_prorata_tax_and_tip(self):
        """
        Cas d'exemple officiel du rapport (Page 18) :
        Sous-total items = 42.0 (Pizza 24.0 Alice, Salade 12.0 Bob, Boisson 6.0 Bob)
        Taxe + pourboire = 8.4
        Total = 50.4 TND
        Résultat attendu : Alice = 28.800 TND, Bob = 21.600 TND, Charlie = 0.000 TND.
        """
        expense = Expense.objects.create(
            group=self.group,
            payer=self.m_alice,
            title="Restaurant Le Djerbien",
            amount=Decimal("50.400"),
            tax_amount=Decimal("5.000"),
            tip_amount=Decimal("3.400"),
            split_rule=Expense.SplitRule.BY_ITEM,
        )

        item_pizza = ExpenseItem.objects.create(expense=expense, label="Pizza", unit_price=Decimal("24.000"), quantity=1)
        item_salade = ExpenseItem.objects.create(expense=expense, label="Salade", unit_price=Decimal("12.000"), quantity=1)
        item_boisson = ExpenseItem.objects.create(expense=expense, label="Boisson", unit_price=Decimal("6.000"), quantity=1)

        # Alice réclame la pizza
        ItemClaim.objects.create(item=item_pizza, member=self.m_alice)
        # Bob réclame la salade et la boisson
        ItemClaim.objects.create(item=item_salade, member=self.m_bob)
        ItemClaim.objects.create(item=item_boisson, member=self.m_bob)

        expense.compute_shares()

        share_alice = ExpenseShare.objects.get(expense=expense, member=self.m_alice)
        share_bob = ExpenseShare.objects.get(expense=expense, member=self.m_bob)
        share_charlie = ExpenseShare.objects.filter(expense=expense, member=self.m_charlie).first()

        self.assertEqual(share_alice.amount_due, Decimal("28.800"))
        self.assertEqual(share_bob.amount_due, Decimal("21.600"))
        self.assertEqual(share_charlie.amount_due, Decimal("0.000"))
        self.assertEqual(share_alice.amount_due + share_bob.amount_due, Decimal("50.400"))

    def test_shared_item_split_between_claimants(self):
        """Un article réclamé par deux personnes est partagé à 50/50 entre elles."""
        expense = Expense.objects.create(
            group=self.group,
            payer=self.m_alice,
            title="Apéro partagé",
            amount=Decimal("20.000"),
            split_rule=Expense.SplitRule.BY_ITEM,
        )
        item = ExpenseItem.objects.create(expense=expense, label="Plat mixte", unit_price=Decimal("20.000"), quantity=1)

        ItemClaim.objects.create(item=item, member=self.m_alice)
        ItemClaim.objects.create(item=item, member=self.m_bob)

        expense.compute_shares()

        share_alice = ExpenseShare.objects.get(expense=expense, member=self.m_alice)
        share_bob = ExpenseShare.objects.get(expense=expense, member=self.m_bob)

        self.assertEqual(share_alice.amount_due, Decimal("10.000"))
        self.assertEqual(share_bob.amount_due, Decimal("10.000"))

    def test_service_get_group_expenses_filtering(self):
        """Le service get_group_expenses filtre correctement par groupe, catégorie et période."""
        cat2 = Category.objects.create(group=self.group, name="Transport")
        date_oct = timezone.localdate().replace(year=2026, month=10, day=15)
        date_nov = timezone.localdate().replace(year=2026, month=11, day=1)

        exp1 = Expense.objects.create(
            group=self.group,
            payer=self.m_alice,
            title="Dépense 1",
            amount=Decimal("15.000"),
            category=self.category,
            expense_date=date_oct,
        )
        exp2 = Expense.objects.create(
            group=self.group,
            payer=self.m_bob,
            title="Dépense 2",
            amount=Decimal("25.000"),
            category=cat2,
            expense_date=date_oct,
        )
        exp3 = Expense.objects.create(
            group=self.group,
            payer=self.m_alice,
            title="Dépense 3",
            amount=Decimal("35.000"),
            category=self.category,
            expense_date=date_nov,
        )

        # Filtre par catégorie
        qs_cat = get_group_expenses(self.group, category=self.category)
        self.assertEqual(set(qs_cat), {exp1, exp3})

        # Filtre par période (YYYY-MM)
        qs_period = get_group_expenses(self.group, period="2026-10")
        self.assertEqual(set(qs_period), {exp1, exp2})


class ExpenseViewTests(TestCase):
    """Tests d'intégration des vues CRUD et permissions (Yahya)."""

    def setUp(self):
        self.user = User.objects.create_user(username="yahya", email="yahya@test.com", password="PassWord123!")
        self.other_user = User.objects.create_user(username="stranger", email="stranger@test.com", password="PassWord123!")

        self.group = Group.objects.create(name="Voyage Sud", currency="TND", created_by=self.user)
        self.membership = Membership.objects.create(user=self.user, group=self.group, role="admin")

        self.category = Category.objects.create(group=self.group, name="Hébergement")
        self.expense = Expense.objects.create(
            group=self.group,
            payer=self.membership,
            title="Hôtel",
            amount=Decimal("120.000"),
            category=self.category,
        )
        self.client.force_login(self.user)

    def test_list_view_renders_group_expenses(self):
        """Un membre accède à la liste des dépenses du groupe."""
        url = reverse("expenses:list", kwargs={"gid": self.group.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hôtel")
        self.assertContains(response, "120,000")


    def test_detail_view_renders_correctly(self):
        """Un membre accède au détail d'une dépense."""
        url = reverse("expenses:detail", kwargs={"gid": self.group.id, "pk": self.expense.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hôtel")
        self.assertContains(response, "Lignes du ticket")

    def test_create_expense_success_and_computes_shares(self):
        """La création d'une dépense valide calcule automatiquement les parts."""
        url = reverse("expenses:create", kwargs={"gid": self.group.id})
        payload = {
            "title": "Essence voiture",
            "amount": "60.000",
            "currency": "TND",
            "expense_date": timezone.localdate().isoformat(),
            "payer": self.membership.id,
            "split_rule": "equal",
            "tax_amount": "0.000",
            "tip_amount": "0.000",
        }
        response = self.client.post(url, payload)
        new_expense = Expense.objects.filter(title="Essence voiture").first()
        self.assertIsNotNone(new_expense)
        self.assertRedirects(response, reverse("expenses:detail", kwargs={"gid": self.group.id, "pk": new_expense.pk}))
        # Vérifie que compute_shares a été exécuté
        self.assertTrue(new_expense.shares.exists())

    def test_update_expense_success(self):
        """Le payeur ou admin peut modifier la dépense."""
        url = reverse("expenses:edit", kwargs={"gid": self.group.id, "pk": self.expense.pk})
        payload = {
            "title": "Hôtel 4 Étoiles",
            "amount": "150.000",
            "currency": "TND",
            "expense_date": timezone.localdate().isoformat(),
            "payer": self.membership.id,
            "split_rule": "equal",
            "tax_amount": "0.000",
            "tip_amount": "0.000",
        }
        response = self.client.post(url, payload)
        self.assertRedirects(response, reverse("expenses:detail", kwargs={"gid": self.group.id, "pk": self.expense.pk}))
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.title, "Hôtel 4 Étoiles")
        self.assertEqual(self.expense.amount, Decimal("150.000"))

    def test_delete_expense_success(self):
        """Le payeur ou admin peut supprimer la dépense."""
        url = reverse("expenses:delete", kwargs={"gid": self.group.id, "pk": self.expense.pk})
        response = self.client.post(url)
        self.assertRedirects(response, reverse("expenses:list", kwargs={"gid": self.group.id}))
        self.assertFalse(Expense.objects.filter(pk=self.expense.pk).exists())

    def test_non_member_forbidden_403(self):
        """Un utilisateur non-membre du groupe reçoit un 403 Forbidden."""
        self.client.force_login(self.other_user)
        url = reverse("expenses:list", kwargs={"gid": self.group.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_add_item_and_toggle_claim(self):
        """Ajout d'un article et bascule de réclamation (claim)."""
        add_url = reverse("expenses:item_add", kwargs={"gid": self.group.id, "pk": self.expense.pk})
        item_payload = {"label": "Café", "unit_price": "3.500", "quantity": "2"}
        response = self.client.post(add_url, item_payload)
        self.assertRedirects(response, reverse("expenses:detail", kwargs={"gid": self.group.id, "pk": self.expense.pk}))

        item = ExpenseItem.objects.filter(expense=self.expense, label="Café").first()
        self.assertIsNotNone(item)
        self.assertEqual(item.total, Decimal("7.000"))

        # Réclamation de l'article par Yahya
        claim_url = reverse("expenses:item_claim", kwargs={"gid": self.group.id, "pk": self.expense.pk, "item_id": item.id})
        self.client.post(claim_url)
        self.assertTrue(ItemClaim.objects.filter(item=item, member=self.membership).exists())

        # Dé-réclamation
        self.client.post(claim_url)
        self.assertFalse(ItemClaim.objects.filter(item=item, member=self.membership).exists())
