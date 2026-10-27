import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from groups.models import Group
from settlements.models import RepaymentPlan, Settlement


class DemoCommandTests(TestCase):
    @override_settings(DEBUG=True)
    def test_demo_group_includes_imem_with_m3_activity(self):
        call_command("m3_demo", stdout=StringIO())
        group = Group.objects.get(name="Coloc démo M3")
        names = set(group.active_memberships().values_list("user__username", flat=True))
        self.assertEqual(names, {"demo_ala", "demo_yahya", "demo_linda", "demo_zahra", "demo_wassim", "demo_imem"})

        imem = get_user_model().objects.get(username="demo_imem")
        self.assertTrue(imem.check_password("finova-demo-2026"))
        # Imem a un remboursement à confirmer (bénéficiaire) et un remboursement déjà confirmé (payeur)
        self.assertTrue(Settlement.objects.filter(group=group, receiver__user=imem, status="pending").exists())
        self.assertTrue(Settlement.objects.filter(group=group, payer__user=imem, status="confirmed").exists())
        self.assertEqual(RepaymentPlan.objects.filter(settlement__group=group).count(), 1)

    @override_settings(DEBUG=False)
    def test_refused_outside_development(self):
        with patch.dict(os.environ, {"LOAD_DEMO_DATA": ""}), self.assertRaises(CommandError):
            call_command("m3_demo")

    @override_settings(DEBUG=False)
    def test_allowed_in_production_on_explicit_opt_in(self):
        with patch.dict(os.environ, {"LOAD_DEMO_DATA": "true"}):
            call_command("m3_demo", stdout=StringIO())
        self.assertTrue(Group.objects.filter(name="Coloc démo M3").exists())
