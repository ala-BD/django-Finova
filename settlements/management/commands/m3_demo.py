"""Données de démonstration M3 (développement uniquement).

    python manage.py m3_demo            # crée le groupe de démo s'il n'existe pas
    python manage.py m3_demo --reset    # le recrée

Comptes : demo_ala, demo_yahya, demo_linda, demo_zahra, demo_wassim — mot de passe affiché à la fin.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from groups.models import Group, Membership
from settlements import services
from settlements.models import RepaymentPlan, Settlement

GROUP_NAME = "Coloc démo M3"
PASSWORD = "finova-demo-2026"


class Command(BaseCommand):
    help = "Crée un groupe de démonstration pour le module M3 (soldes et remboursements)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Supprime puis recrée le groupe de démo.")

    @transaction.atomic
    def handle(self, *args, reset=False, **options):
        if not settings.DEBUG:
            raise CommandError("Commande réservée au développement (DEBUG=True).")
        existing = Group.objects.filter(name=GROUP_NAME)
        if existing.exists():
            if not reset:
                self.stdout.write(f"« {GROUP_NAME} » existe déjà (utilisez --reset pour le recréer).")
                self.print_access(existing.first())
                return
            Settlement.objects.filter(group__in=existing).delete()
            existing.delete()

        User = get_user_model()
        users = {}
        for username, first_name, profile in [
            ("demo_ala", "Ala", "student"),
            ("demo_yahya", "Yahya", "active"),
            ("demo_linda", "Linda", "senior"),
            ("demo_zahra", "Zahra", "active"),
            ("demo_wassim", "Wassim", "student"),
        ]:
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"first_name": first_name, "email": f"{username}@example.com",
                          "income_profile": profile},
            )
            user.set_password(PASSWORD)
            user.save()
            users[first_name] = user

        group = Group.objects.create(name=GROUP_NAME, created_by=users["Ala"],
                                     description="Données générées pour la démo de M3.")
        weights = {"student": Decimal("1"), "active": Decimal("2"), "senior": Decimal("3")}
        m = {
            name: Membership.objects.create(
                group=group, user=user,
                role=Membership.Role.ADMIN if name == "Ala" else Membership.Role.MEMBER,
                weight=weights[user.income_profile],
            )
            for name, user in users.items()
        }

        # 1. Remboursements confirmés : ils déterminent les soldes (M2 non encore branché).
        #    Ils forment des cycles : 7 dettes brutes → 4 virements après simplification.
        for payer, receiver, amount in [
            ("Linda", "Ala", "40"), ("Yahya", "Linda", "25.500"), ("Zahra", "Yahya", "30"),
            ("Wassim", "Zahra", "18"), ("Ala", "Wassim", "12"), ("Yahya", "Ala", "20"),
            ("Zahra", "Linda", "15"),
        ]:
            settlement = services.create_settlement(group, m[payer], m[receiver], Decimal(amount))
            services.confirm_settlement(settlement, users[receiver])

        # 2. Remboursement en attente de confirmation par Ala
        services.create_settlement(group, m["Yahya"], m["Ala"], Decimal("60"), note="Virement du loyer")

        # 3. Remboursement avec échéancier commencé il y a 40 jours, première échéance payée en retard
        settlement = services.create_settlement(group, m["Linda"], m["Yahya"], Decimal("150"))
        plan = RepaymentPlan(settlement=settlement, installments_count=3,
                             frequency=RepaymentPlan.Frequency.MONTHLY,
                             start_date=timezone.localdate() - timedelta(days=40))
        services.save_plan(plan)
        first = plan.installments.first()
        first.register_payment(first.amount, today=first.due_date + timedelta(days=6))
        services.predict_risk(plan)

        self.stdout.write(self.style.SUCCESS(f"Groupe « {GROUP_NAME} » créé."))
        self.print_access(group)

    def print_access(self, group):
        self.stdout.write(
            f"Connexion : demo_ala, demo_yahya, demo_linda, demo_zahra, demo_wassim — mot de passe : {PASSWORD}\n"
            f"Soldes : http://127.0.0.1:8000/groups/{group.pk}/balances/\n"
            f"Remboursements : http://127.0.0.1:8000/groups/{group.pk}/settlements/"
        )
