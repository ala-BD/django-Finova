from datetime import timedelta
from decimal import Decimal
import smtplib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core import mail
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from groups.models import Group, Invitation, Membership
from groups.services import can_leave, preview_split, split_by_weight, split_equally


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

    def test_split_equally(self):
        parts = split_equally(Decimal("90"), ["a", "b", "c"])
        self.assertEqual(parts["a"], Decimal("30.000"))
        self.assertEqual(sum(parts.values()), Decimal("90.000"))

    @patch("core.ai.call_llm", return_value="Le partage égal répartit le coût également.")
    def test_equity_coach_uses_exact_application_shares(self, mock_llm):
        from groups.services import compare_equity_rules

        admin = User.objects.create_user(username="coach-admin", email="coach-admin@example.com")
        member = User.objects.create_user(username="coach-member", email="coach-member@example.com")
        group = Group.objects.create(name="Coach")
        Membership.objects.create(
            group=group,
            user=admin,
            role=Membership.Role.ADMIN,
            weight=Decimal("1"),
        )
        Membership.objects.create(
            group=group,
            user=member,
            role=Membership.Role.MEMBER,
            weight=Decimal("2"),
        )

        result = compare_equity_rules(group, Decimal("90"), user=admin)

        self.assertEqual(
            [share["share"] for share in result["equal"]],
            [Decimal("45.000"), Decimal("45.000")],
        )
        self.assertEqual(
            [share["share"] for share in result["by_weight"]],
            [Decimal("60.000"), Decimal("30.000")],
        )
        self.assertEqual(sum(share["share"] for share in result["equal"]), Decimal("90.000"))
        self.assertEqual(sum(share["share"] for share in result["by_weight"]), Decimal("90.000"))
        self.assertEqual(result["explanation"], "Le partage égal répartit le coût également.")
        mock_llm.assert_called_once()
        self.assertIn('"amount": "90"', mock_llm.call_args.kwargs["prompt"])

    @patch(
        "core.ai.call_llm",
        return_value="Bonjour, rejoins le groupe [LIEN D'INVITATION].",
    )
    def test_invitation_message_is_generated_without_recipient_email(self, mock_llm):
        from groups.services import generate_invitation_message

        group = Group.objects.create(
            name="Voyage",
            description="Vacances entre amis",
            default_split_rule=Group.SplitRule.BY_WEIGHT,
        )

        message = generate_invitation_message(group)

        self.assertIn("[LIEN D'INVITATION]", message)
        self.assertIn('"name": "Voyage"', mock_llm.call_args.kwargs["prompt"])
        self.assertNotIn("recipient_email", mock_llm.call_args.kwargs["prompt"])

    def test_preview_by_weight(self):
        admin = User.objects.create_user(username="p1", email="p1@example.com", password="x")
        member = User.objects.create_user(username="p2", email="p2@example.com", password="x")
        group = Group.objects.create(
            name="Équité",
            default_split_rule=Group.SplitRule.BY_WEIGHT,
        )
        Membership.objects.create(
            group=group, user=admin, role=Membership.Role.ADMIN, weight=Decimal("1")
        )
        Membership.objects.create(
            group=group, user=member, role=Membership.Role.MEMBER, weight=Decimal("2")
        )
        preview = preview_split(group, Decimal("90"))
        shares = {row["membership"].user_id: row["share"] for row in preview["rows"]}
        self.assertEqual(shares[admin.pk], Decimal("30.000"))
        self.assertEqual(shares[member.pk], Decimal("60.000"))

    def test_consumption_falls_back_to_equal(self):
        user = User.objects.create_user(username="p3", email="p3@example.com", password="x")
        other = User.objects.create_user(username="p4", email="p4@example.com", password="x")
        group = Group.objects.create(
            name="Tickets",
            default_split_rule=Group.SplitRule.BY_CONSUMPTION,
        )
        Membership.objects.create(group=group, user=user, role=Membership.Role.ADMIN)
        Membership.objects.create(group=group, user=other, role=Membership.Role.MEMBER)
        preview = preview_split(group, Decimal("90"))
        self.assertEqual(preview["used_rule"], Group.SplitRule.EQUAL)
        self.assertIn("repli", preview["note"].lower())

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

    def test_list_search_matches_group_name_and_description(self):
        group = Group.objects.create(
            name="Week-end",
            description="Séjour à Sousse",
            created_by=self.admin,
        )
        Membership.objects.create(
            group=group,
            user=self.admin,
            role=Membership.Role.ADMIN,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("groups:list"), {"q": "sousse"})

        self.assertEqual(
            [result.pk for result in response.context["groups"]],
            [group.pk],
        )
        self.assertEqual(response.context["search_query"], "sousse")

    def test_list_filters_by_currency_and_split_rule_with_search(self):
        matching_group = Group.objects.create(
            name="Voyage Sousse",
            description="Séjour entre amis",
            currency=Group.Currency.EUR,
            default_split_rule=Group.SplitRule.BY_WEIGHT,
            created_by=self.admin,
        )
        Membership.objects.create(
            group=matching_group,
            user=self.admin,
            role=Membership.Role.ADMIN,
        )
        non_matching_group = Group.objects.create(
            name="Voyage Sousse en TND",
            currency=Group.Currency.TND,
            default_split_rule=Group.SplitRule.BY_WEIGHT,
            created_by=self.admin,
        )
        Membership.objects.create(
            group=non_matching_group,
            user=self.admin,
            role=Membership.Role.ADMIN,
        )
        archived_group = Group.objects.create(
            name="Voyage Sousse archivé",
            currency=Group.Currency.EUR,
            default_split_rule=Group.SplitRule.BY_WEIGHT,
            is_archived=True,
            created_by=self.admin,
        )
        Membership.objects.create(
            group=archived_group,
            user=self.admin,
            role=Membership.Role.ADMIN,
        )
        other_user_group = Group.objects.create(
            name="Voyage Sousse autre utilisateur",
            currency=Group.Currency.EUR,
            default_split_rule=Group.SplitRule.BY_WEIGHT,
        )
        Membership.objects.create(
            group=other_user_group,
            user=self.outsider,
            role=Membership.Role.ADMIN,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("groups:list"),
            {
                "q": "sousse",
                "currency": Group.Currency.EUR,
                "split_rule": Group.SplitRule.BY_WEIGHT,
            },
        )

        self.assertEqual(
            [result.pk for result in response.context["groups"]],
            [matching_group.pk],
        )
        self.assertEqual(response.context["selected_currency"], Group.Currency.EUR)
        self.assertEqual(
            response.context["selected_split_rule"],
            Group.SplitRule.BY_WEIGHT,
        )

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

    def test_apply_profile_weight(self):
        self.member.income_profile = User.IncomeProfile.SENIOR
        self.member.save()
        self.client.force_login(self.admin)
        url = reverse(
            "groups:member_edit",
            kwargs={"gid": self.group.pk, "mid": self.member_membership.pk},
        )
        self.client.post(
            url,
            {"role": "member", "weight": "9.000", "use_profile_weight": "on"},
        )
        self.member_membership.refresh_from_db()
        self.assertEqual(self.member_membership.weight, Decimal("3.000"))

    def test_equity_preview_on_detail(self):
        self.group.default_split_rule = Group.SplitRule.BY_WEIGHT
        self.group.save()
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("groups:detail", kwargs={"gid": self.group.pk}),
            {"amount": "90"},
        )
        self.assertContains(response, "Profils d'équité")
        self.assertContains(response, "Coach d'équité IA")
        self.assertContains(response, "30,000")
        self.assertContains(response, "60,000")


class InvitationViewTests(GroupFixturesMixin, TestCase):
    def test_invite_page_offers_ai_message_draft(self):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("groups:invite", kwargs={"gid": self.group.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rédiger un message avec l'IA")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        PUBLIC_BASE_URL="https://finova.example.com",
    )
    def test_invitation_is_emailed_with_public_acceptance_link(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            {"email": "guest@example.com"},
            follow=True,
        )

        invitation = Invitation.objects.get(email="guest@example.com")
        accept_url = (
            f"https://finova.example.com"
            f"{reverse('invitations:accept', kwargs={'token': invitation.token})}"
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["guest@example.com"])
        self.assertIn(accept_url, mail.outbox[0].body)
        self.assertTrue(
            any("envoyée par e-mail" in str(message) for message in response.context["messages"])
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        PUBLIC_BASE_URL="",
    )
    def test_console_backend_does_not_claim_email_was_sent(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            {"email": "guest@example.com"},
            follow=True,
        )

        self.assertTrue(
            any(
                "aucun e-mail n'a été envoyé" in str(message)
                for message in response.context["messages"]
            )
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        PUBLIC_BASE_URL="https://finova.example.com",
    )
    @patch("groups.views.send_mail", side_effect=smtplib.SMTPException("SMTP down"))
    def test_smtp_failure_is_reported_and_invitation_link_is_preserved(
        self, _mock_send_mail
    ):
        self.client.force_login(self.admin)

        with self.assertLogs("groups.views", level="ERROR"):
            response = self.client.post(
                reverse("groups:invite", kwargs={"gid": self.group.pk}),
                {"email": "guest@example.com"},
                follow=True,
            )

        invitation = Invitation.objects.get(email="guest@example.com")
        accept_url = (
            f"https://finova.example.com"
            f"{reverse('invitations:accept', kwargs={'token': invitation.token})}"
        )
        messages_text = " ".join(
            str(message) for message in response.context["messages"]
        )
        self.assertIn("l'e-mail n'a pas pu être envoyé", messages_text)
        self.assertIn(accept_url, messages_text)

    @patch(
        "groups.views.generate_invitation_message",
        return_value="Bonjour, rejoins le groupe [LIEN D'INVITATION].",
    )
    def test_admin_can_generate_invitation_draft_without_creating_invitation(
        self, mock_generate
    ):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            {"action": "generate_message", "email": "new@example.com"},
        )

        self.assertEqual(response.status_code, 200)
        data = _json.loads(response.content)
        self.assertTrue(data["ok"])
        self.assertIn("[LIEN D'INVITATION]", data["message"])
        self.assertFalse(Invitation.objects.filter(group=self.group).exists())
        mock_generate.assert_called_once()

    @patch("groups.views.generate_invitation_message")
    def test_non_admin_cannot_generate_invitation_draft(self, mock_generate):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            {"action": "generate_message", "email": "new@example.com"},
        )

        self.assertEqual(response.status_code, 403)
        mock_generate.assert_not_called()

    @patch("groups.views.generate_invitation_message")
    def test_invalid_email_is_rejected_before_generating_draft(self, mock_generate):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("groups:invite", kwargs={"gid": self.group.pk}),
            {"action": "generate_message", "email": "not-an-email"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(_json.loads(response.content)["ok"])
        mock_generate.assert_not_called()

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


class EquityCoachViewTests(GroupFixturesMixin, TestCase):
    @patch("core.ai.call_llm", return_value="Les parts pondérées suivent les poids choisis.")
    def test_member_can_compare_equity_rules(self, mock_llm):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("groups:equity_coach", kwargs={"gid": self.group.pk}),
            {"amount": "90"},
        )

        self.assertEqual(response.status_code, 200)
        data = _json.loads(response.content)
        self.assertTrue(data["ok"])
        self.assertEqual(
            [share["share"] for share in data["comparison"]["equal"]],
            ["45.000", "45.000"],
        )
        self.assertEqual(
            [share["share"] for share in data["comparison"]["by_weight"]],
            ["60.000", "30.000"],
        )
        mock_llm.assert_called_once()

    @patch("core.ai.call_llm")
    def test_non_member_cannot_use_equity_coach(self, mock_llm):
        self.client.force_login(self.outsider)

        response = self.client.post(
            reverse("groups:equity_coach", kwargs={"gid": self.group.pk}),
            {"amount": "90"},
        )

        self.assertEqual(response.status_code, 403)
        mock_llm.assert_not_called()

    @patch("core.ai.call_llm")
    def test_equity_coach_rejects_invalid_amount(self, mock_llm):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("groups:equity_coach", kwargs={"gid": self.group.pk}),
            {"amount": "-1"},
        )

        self.assertEqual(response.status_code, 400)
        mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# Tests IA — génération de groupe par LLM (aucun appel réseau dans les tests)
# ---------------------------------------------------------------------------

import json as _json

VALID_LLM_RESPONSE = _json.dumps({
    "name": "Voyage Djerba",
    "description": "Voyage de 5 amis à Djerba en été",
    "currency": "TND",
    "default_split_rule": "equal",
    "categories": ["Transport", "Hébergement", "Nourriture"],
    "budgets": [
        {"category": "Transport", "amount": 500},
        {"category": "Hébergement", "amount": 1200},
        {"category": "Nourriture", "amount": 300},
    ],
})

INVALID_JSON_RESPONSE = "Voici le groupe que je vous suggère : {non_valide"

TRUNCATED_JSON_RESPONSE = '{"name": "Djerba", "description": "Voyage"'  # JSON tronqué


class AIGroupServiceTests(TestCase):
    """Tests du service generate_group_from_prompt — aucun appel réseau."""

    @patch("core.ai.call_llm", return_value=VALID_LLM_RESPONSE)
    def test_valid_json_returns_group_data(self, mock_llm):
        from groups.services import generate_group_from_prompt
        result = generate_group_from_prompt("voyage de 5 amis à Djerba, budget 2000 TND")
        self.assertEqual(result["name"], "Voyage Djerba")
        self.assertEqual(result["currency"], "TND")
        self.assertEqual(result["default_split_rule"], "equal")
        self.assertIn("Transport", result["categories"])
        self.assertEqual(len(result["budgets"]), 3)
        mock_llm.assert_called_once()

    @patch("core.ai.call_llm", return_value=INVALID_JSON_RESPONSE)
    def test_invalid_json_raises_value_error_after_retries(self, mock_llm):
        from groups.services import generate_group_from_prompt
        with self.assertRaises(ValueError) as ctx:
            generate_group_from_prompt("test invalide", max_retries=1)
        self.assertIn("JSON", str(ctx.exception))
        # 1 essai initial + 1 retry = 2 appels
        self.assertEqual(mock_llm.call_count, 2)

    @patch("core.ai.call_llm", return_value=TRUNCATED_JSON_RESPONSE)
    def test_truncated_json_raises_value_error(self, mock_llm):
        from groups.services import generate_group_from_prompt
        with self.assertRaises(ValueError):
            generate_group_from_prompt("test tronqué", max_retries=0)

    @patch("core.ai.call_llm")
    def test_llm_error_propagates_immediately(self, mock_llm):
        from core.ai import LLMError
        from groups.services import generate_group_from_prompt
        mock_llm.side_effect = LLMError("LLM_API_KEY manquante.")
        with self.assertRaises(LLMError):
            generate_group_from_prompt("test erreur réseau")
        # Pas de retry sur erreur LLM
        mock_llm.assert_called_once()

    @patch("core.ai.call_llm", return_value=VALID_LLM_RESPONSE)
    def test_currency_normalised_to_uppercase(self, _mock):
        from groups.services import validate_group_proposal
        data = _json.loads(VALID_LLM_RESPONSE)
        data["currency"] = "tnd"
        result = validate_group_proposal(data)
        self.assertEqual(result["currency"], "TND")

    @patch("core.ai.call_llm", return_value=VALID_LLM_RESPONSE)
    def test_invalid_rule_is_rejected(self, _mock):
        from groups.services import validate_group_proposal
        data = _json.loads(VALID_LLM_RESPONSE)
        data["default_split_rule"] = "random_rule"
        with self.assertRaisesRegex(ValueError, "règle de partage"):
            validate_group_proposal(data)

    @patch("core.ai.call_llm", return_value=VALID_LLM_RESPONSE)
    def test_name_too_short_raises_value_error(self, _mock):
        from groups.services import validate_group_proposal
        data = _json.loads(VALID_LLM_RESPONSE)
        data["name"] = "ab"
        with self.assertRaises(ValueError) as ctx:
            validate_group_proposal(data)
        self.assertIn("80", str(ctx.exception))

    def test_invalid_currency_is_rejected(self):
        from groups.services import validate_group_proposal
        data = _json.loads(VALID_LLM_RESPONSE)
        data["currency"] = "XYZ"
        with self.assertRaisesRegex(ValueError, "devise"):
            validate_group_proposal(data)

    def test_category_count_and_budget_are_validated(self):
        from groups.services import validate_group_proposal
        data = _json.loads(VALID_LLM_RESPONSE)
        data["categories"] = ["Transport"]
        with self.assertRaises(ValueError) as ctx:
            validate_group_proposal(data)
        self.assertIn("6", str(ctx.exception))

        data = _json.loads(VALID_LLM_RESPONSE)
        data["budgets"][0]["amount"] = -1
        with self.assertRaisesRegex(ValueError, "montant positif"):
            validate_group_proposal(data)


class AIGroupViewTests(GroupFixturesMixin, TestCase):
    """Tests de la vue GroupAICreateView."""

    def test_get_ai_create_page_requires_login(self):
        response = self.client.get(reverse("groups:ai_create"))
        self.assertRedirects(response, f"/accounts/login/?next=/groups/new/ai/")

    def test_get_ai_create_page_ok_for_logged_in(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("groups:ai_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "assistant")
        self.assertContains(response, "Confirmer et créer le groupe")
        self.assertContains(response, 'id="proposal-form"')

    @patch("groups.views.generate_group_from_prompt")
    def test_post_valid_prompt_returns_proposal_without_creating_group(self, mock_gen):
        mock_gen.return_value = {
            "name": "Voyage Djerba",
            "description": "Vacances en été",
            "currency": "TND",
            "default_split_rule": "equal",
            "categories": ["Transport", "Hébergement"],
            "budgets": [{"category": "Transport", "amount": 500}],
        }
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:ai_create"),
            {"prompt": "voyage de 5 amis à Djerba, budget 2000 TND"},
        )
        self.assertEqual(response.status_code, 200)
        data = _json.loads(response.content)
        self.assertTrue(data["ok"])
        self.assertEqual(data["proposal"]["name"], "Voyage Djerba")
        self.assertFalse(Group.objects.filter(name="Voyage Djerba").exists())

    def test_post_confirm_creates_user_edited_proposal(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:ai_create"),
            {
                "action": "confirm",
                "name": "Voyage confirmé",
                "description": "Description modifiée",
                "currency": "EUR",
                "default_split_rule": Group.SplitRule.BY_WEIGHT,
                "categories": ["Transport", "Hébergement"],
                "budget_category": ["Transport"],
                "budget_amount": ["450.500"],
            },
        )

        self.assertEqual(response.status_code, 200)
        data = _json.loads(response.content)
        self.assertTrue(data["ok"])
        group = Group.objects.get(name="Voyage confirmé")
        self.assertEqual(group.description, "Description modifiée")
        self.assertEqual(group.currency, Group.Currency.EUR)
        self.assertEqual(group.default_split_rule, Group.SplitRule.BY_WEIGHT)
        self.assertTrue(
            Membership.objects.filter(
                group=group, user=self.admin, role=Membership.Role.ADMIN
            ).exists()
        )

    def test_post_confirm_rejects_invalid_proposal(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:ai_create"),
            {
                "action": "confirm",
                "name": "Groupe invalide",
                "description": "",
                "currency": "XYZ",
                "default_split_rule": Group.SplitRule.EQUAL,
                "categories": ["Transport", "Hébergement"],
                "budget_category": [],
                "budget_amount": [],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(Group.objects.filter(name="Groupe invalide").exists())

    def test_post_empty_prompt_returns_400(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:ai_create"),
            {"prompt": "   "},
        )
        self.assertEqual(response.status_code, 400)
        data = _json.loads(response.content)
        self.assertFalse(data["ok"])

    @patch("groups.views.generate_group_from_prompt")
    def test_post_llm_error_returns_503(self, mock_gen):
        from core.ai import LLMError
        mock_gen.side_effect = LLMError("LLM_API_KEY manquante.")
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:ai_create"),
            {"prompt": "colocation à Tunis"},
        )
        self.assertEqual(response.status_code, 503)
        data = _json.loads(response.content)
        self.assertFalse(data["ok"])
        self.assertFalse(data["retry"])

    @patch("groups.views.generate_group_from_prompt")
    def test_post_invalid_json_returns_422_with_retry(self, mock_gen):
        mock_gen.side_effect = ValueError("JSON invalide après 3 tentatives.")
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("groups:ai_create"),
            {"prompt": "test"},
        )
        self.assertEqual(response.status_code, 422)
        data = _json.loads(response.content)
        self.assertFalse(data["ok"])
        self.assertTrue(data["retry"])
