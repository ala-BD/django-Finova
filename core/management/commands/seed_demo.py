"""
Données de démonstration (Linda) : utilisateurs, groupe, catégories, budgets et
sept mois de dépenses générées. Les montants sont pseudo-aléatoires (graine
fixe) : la démo est reproductible, et on l'annonce comme jeu généré.

    python manage.py seed_demo            # crée les données si absentes
    python manage.py seed_demo --reset    # supprime puis recrée
"""

import calendar
import random
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from budgets.models import Budget, Category
from budgets.services import add_months, check_budget_alerts, first_of_month
from expenses.models import Expense
from groups.models import Group, Membership, SplitRule

DEMO_PASSWORD = "Finova-demo-2026"
DEMO_GROUP = "Coloc Lac 2 (démo)"
HISTORY_MONTHS = 6

# username, prénom, profil de revenu, poids, rôle
TEAM = [
    ("linda", "Linda", "active", Decimal("2"), Membership.Role.ADMIN),
    ("ala", "Ala", "active", Decimal("2"), Membership.Role.ADMIN),
    ("yahya", "Yahya", "student", Decimal("1"), Membership.Role.MEMBER),
    ("imem", "Imem", "student", Decimal("1"), Membership.Role.MEMBER),
    ("zahra", "Zahra", "senior", Decimal("3"), Membership.Role.MEMBER),
    ("wassim", "Wassim", "active", Decimal("2"), Membership.Role.MEMBER),
]

# nom, icône, couleur, limite mensuelle, report, libellés possibles
CATEGORIES = [
    ("Alimentation", "🛒", "#15803d", Decimal("650"), False, ["Carrefour", "Monoprix", "Marché central", "Boulangerie"]),
    ("Restaurants", "🍽️", "#b45309", Decimal("280"), False, ["Pizzeria", "Resto Lac", "Café", "Sandwicherie"]),
    ("Transport", "🚌", "#1d4ed8", Decimal("180"), False, ["Taxi", "Bolt", "Métro", "Essence"]),
    ("Logement", "🏠", "#7c3aed", Decimal("1250"), False, ["Loyer"]),
    ("Factures", "💡", "#0e7490", Decimal("260"), False, ["STEG", "SONEDE", "Internet"]),
    ("Loisirs", "🎬", "#be185d", Decimal("150"), True, ["Cinéma", "Bowling", "Concert", "Abonnement streaming"]),
]

# nombre moyen d'achats par mois et montant moyen d'un achat
PATTERNS = {
    "Alimentation": (14, 42),
    "Restaurants": (7, 38),
    "Transport": (12, 13),
    "Loisirs": (4, 36),
}


class Command(BaseCommand):
    help = "Crée un jeu de démonstration (utilisateurs, groupe, catégories, budgets, dépenses)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Supprime les données de démo existantes.")
        parser.add_argument("--seed", type=int, default=42, help="Graine du générateur (défaut : 42).")

    @transaction.atomic
    def handle(self, *args, reset=False, seed=42, **options):
        User = get_user_model()
        if reset:
            # Les dépenses d'abord : leur payeur et leur catégorie sont protégés (PROTECT).
            Expense.objects.filter(group__name=DEMO_GROUP).delete()
            Group.objects.filter(name=DEMO_GROUP).delete()
            User.objects.filter(username__in=[row[0] for row in TEAM]).delete()
        elif Group.objects.filter(name=DEMO_GROUP).exists():
            self.stdout.write(self.style.WARNING("Données de démo déjà présentes (utiliser --reset)."))
            return

        rng = random.Random(seed)
        today = timezone.localdate()
        users = {}
        for username, first_name, profile, _, _ in TEAM:
            user = User.objects.create_user(
                username=username,
                email=f"{username}@finova.demo",
                password=DEMO_PASSWORD,
                first_name=first_name,
                income_profile=profile,
            )
            users[username] = user

        group = Group.objects.create(
            name=DEMO_GROUP,
            description="Colocation de six étudiants et jeunes actifs (données générées).",
            currency="TND",
            default_split_rule=SplitRule.WEIGHTED,
            created_by=users["linda"],
        )
        members = [
            Membership.objects.create(user=users[username], group=group, role=role, weight=weight)
            for username, _, _, weight, role in TEAM
        ]

        categories = {}
        for name, icon, color, _, _, _ in CATEGORIES:
            categories[name] = Category.objects.create(group=group, name=name, icon=icon, color=color)

        start = add_months(first_of_month(today), -HISTORY_MONTHS)
        expenses = []
        month = start
        while month <= first_of_month(today):
            last_day = calendar.monthrange(month.year, month.month)[1]
            until = today.day if month == first_of_month(today) else last_day
            # Mois « chargé » ou « calme » : le niveau varie d'un mois à l'autre.
            mood = rng.uniform(0.8, 1.25)
            for name, _, _, _, _, labels in CATEGORIES:
                category = categories[name]

                def add(day, amount, label):
                    if day <= until:
                        expenses.append(
                            Expense(
                                group=group,
                                payer=rng.choice(members),
                                category=category,
                                title=label,
                                amount=Decimal(str(round(amount, 3))),
                                currency="TND",
                                expense_date=date(month.year, month.month, day),
                                split_rule=SplitRule.WEIGHTED,
                            )
                        )

                if name == "Logement":
                    add(1, 1200, "Loyer")
                elif name == "Factures":
                    add(8, rng.uniform(90, 150) * mood, "STEG")
                    add(12, rng.uniform(25, 45), "SONEDE")
                    add(15, 69.9, "Internet")
                else:
                    count, average = PATTERNS[name]
                    for _ in range(max(1, round(count * mood * rng.uniform(0.85, 1.15)))):
                        add(rng.randint(1, last_day), rng.uniform(0.4, 1.6) * average * mood, rng.choice(labels))
            month = add_months(month, 1)
        Expense.objects.bulk_create(expenses)

        budgets = []
        month = start
        while month <= first_of_month(today):
            for name, _, _, limit, carry_over, _ in CATEGORIES:
                budgets.append(
                    Budget(group=group, category=categories[name], month=month, limit_amount=limit, carry_over=carry_over)
                )
            month = add_months(month, 1)
        Budget.objects.bulk_create(budgets)

        alerts = 0
        for budget in Budget.objects.filter(group=group, month=first_of_month(today)).select_related("group", "category"):
            alerts += len(check_budget_alerts(budget))

        self.stdout.write(
            self.style.SUCCESS(
                f"Groupe « {group.name} » : {len(members)} membres, {len(categories)} catégories, "
                f"{len(budgets)} budgets, {len(expenses)} dépenses, {alerts} alerte(s) envoyée(s)."
            )
        )
        self.stdout.write(f"Connexion : linda / {DEMO_PASSWORD} (admin du groupe), ou yahya (membre simple).")
