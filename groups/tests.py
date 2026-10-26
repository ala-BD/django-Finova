from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from groups.models import Group, Invitation, Membership
from groups.services import can_leave, split_by_weight


User = get_user_model()


class GroupFixturesMixin:
    def setUp(self):
        self.admin = User.objects.create_user(
            username="ala",
            email="ala@example.com",
            password="mot-de-passe-fort",
            income_profile=User.IncomeProfile.STUDENT,
        )
        self.member = User.objects.create_user(
            username="yahya",
            email="yahya@example.com",
            password="mot-de-passe-fort",
        )
        self.outsider = User.objects.create_user(
            username="imem",
            email="imem@example.com",
            password="mot-de-passe-fort",
        )
        self.group = Group.objects.create(
            name="Colocation",
            currency=Group.Currency.TND,
            created_by=self.admin,
        )
        self.admin_membership = Membership.objects.create(
            group=self.group,
            user=self.admin,
            role=Membership.Role.ADMIN,
            weight=Decimal("1.000"),
        )
        self.member_membership = Membership.objects.create(
            group=self.group,
            user=self.member,
            role=Membership.Role.MEMBER,
            weight=Decimal("2.000"),
        )


class MembershipModelTests(GroupFixturesMixin, TestCase):
    def test_weight_must_be_positive(self):
        self.member_membership.weight = Decimal("0")
        with self.assertRaises(ValidationError):
            self.member_membership.full_clean()

    def test_unique_member_per_group(self):
        with self.assertRaises(IntegrityError):
            Membership.objects.create(
                group=self.group,
                user=self.member,
                role=Membership.Role.MEMBER,
            )

    def test_cannot_demote_last_admin(self):
        self.admin_membership.role = Membership.Role.MEMBER
        with self.assertRaises(ValidationError):
            self.admin_membership.full_clean()


class InvitationModelTests(GroupFixturesMixin, TestCase):
    def test_invitation_expires(self):
        invitation = Invitation.objects.create(
            group=self.group,
            email="linda@example.com",
            invited_by=self.admin,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertTrue(invitation.is_expired())
        invitation.refresh_expiration_status()
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, Invitation.Status.EXPIRED)


class ServiceTests(TestCase):
    def test_split_by_weight_example(self):
        parts = split_by_weight(
            Decimal("90"),
            {"etudiant": Decimal("1"), "actif": Decimal("2"), "senior": Decimal("3")},
        )
        self.assertEqual(parts["etudiant"], Decimal("15.000"))
        self.assertEqual(parts["actif"], Decimal("30.000"))
        self.assertEqual(parts["senior"], Decimal("45.000"))
        self.assertEqual(sum(parts.values()), Decimal("90.000"))

    @patch("groups.services.get_member_balance", return_value=Decimal("12.500"))
    def test_cannot_leave_with_nonzero_balance(self, _mock):
        user = User.objects.create_user(username="a", email="a@example.com", password="x")
        group = Group.objects.create(name="Voyage")
        Membership.objects.create(group=group, user=user, role=Membership.Role.MEMBER)
        allowed, reason = can_leave(user, group)
        self.assertFalse(allowed)
        self.assertIn("solde", reason.lower())


class GroupViewTests(GroupFixturesMixin, TestCase):
    def test_list_shows_only_own_groups(self):
        other = Group.objects.create(name="Autre")
        Membership.objects.create(group=other, user=self.outsider, role=Membership.Role.ADMIN)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("groups:list"))
        self.assertContains(response, "Colocation")
        self.assertNotContains(response, "Autre")

    def test_create_group_makes_creator_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:create"),
            {
                "name": "Djerba 2026",
                "description": "Voyage",
                "currency": "TND",
                "default_split_rule": Group.SplitRule.EQUAL,
            },
        )
        group = Group.objects.get(name="Djerba 2026")
        self.assertRedirects(response, reverse("groups:detail", kwargs={"gid": group.pk}))
        membership = Membership.objects.get(group=group, user=self.admin)
        self.assertEqual(membership.role, Membership.Role.ADMIN)
        self.assertEqual(membership.weight, Decimal("1.000"))

    def test_create_rejects_short_name(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:create"),
            {"name": "ab", "currency": "TND", "default_split_rule": "equal"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Group.objects.filter(name="ab").exists())

    def test_non_member_gets_403(self):
        self.client.force_login(self.outsider)
        urls = [
            reverse("groups:detail", kwargs={"gid": self.group.pk}),
            reverse("groups:edit", kwargs={"gid": self.group.pk}),
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            reverse("groups:delete", kwargs={"gid": self.group.pk}),
            reverse("groups:leave", kwargs={"gid": self.group.pk}),
            reverse(
                "groups:member_edit",
                kwargs={"gid": self.group.pk, "mid": self.member_membership.pk},
            ),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_member_cannot_edit_or_invite(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse("groups:edit", kwargs={"gid": self.group.pk})).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(reverse("groups:invite", kwargs={"gid": self.group.pk})).status_code,
            403,
        )

    def test_archive_requires_post(self):
        self.client.force_login(self.admin)
        url = reverse("groups:delete", kwargs={"gid": self.group.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url)
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_archived)
        listing = self.client.get(reverse("groups:list"))
        self.assertQuerySetEqual(listing.context["groups"], [])
        self.assertContains(listing, "aucun groupe")

    def test_update_member_weight(self):
        self.client.force_login(self.admin)
        url = reverse(
            "groups:member_edit",
            kwargs={"gid": self.group.pk, "mid": self.member_membership.pk},
        )
        response = self.client.post(url, {"role": "member", "weight": "3.000"})
        self.assertRedirects(
            response, reverse("groups:detail", kwargs={"gid": self.group.pk})
        )
        self.member_membership.refresh_from_db()
        self.assertEqual(self.member_membership.weight, Decimal("3.000"))


class InvitationViewTests(GroupFixturesMixin, TestCase):
    def test_invite_and_accept(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            {"email": "linda@example.com"},
        )
        invitation = Invitation.objects.get(email="linda@example.com")
        self.assertEqual(invitation.status, Invitation.Status.PENDING)
        self.assertRedirects(
            response, reverse("groups:detail", kwargs={"gid": self.group.pk})
        )

        guest = User.objects.create_user(
            username="linda",
            email="linda@example.com",
            password="mot-de-passe-fort",
        )
        self.client.force_login(guest)
        accept = reverse("invitations:accept", kwargs={"token": invitation.token})
        self.assertEqual(self.client.get(accept).status_code, 200)
        self.client.post(accept)
        self.assertTrue(
            Membership.objects.filter(
                group=self.group, user=guest, is_active=True
            ).exists()
        )
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, Invitation.Status.ACCEPTED)

    def test_refuse_expired_invitation(self):
        invitation = Invitation.objects.create(
            group=self.group,
            email="linda@example.com",
            invited_by=self.admin,
            expires_at=timezone.now() - timedelta(days=1),
        )
        guest = User.objects.create_user(
            username="linda",
            email="linda@example.com",
            password="mot-de-passe-fort",
        )
        self.client.force_login(guest)
        response = self.client.get(
            reverse("invitations:accept", kwargs={"token": invitation.token})
        )
        self.assertContains(response, "expiré")

    def test_duplicate_pending_invitation_rejected(self):
        Invitation.objects.create(
            group=self.group,
            email="linda@example.com",
            invited_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            {"email": "linda@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Invitation.objects.filter(email="linda@example.com").count(), 1
        )
