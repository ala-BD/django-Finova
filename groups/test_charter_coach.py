"""M1 · Charte de groupe générée par IA et coach d'équité interactif. Aucun appel réseau (LLM simulé)."""

import json
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.ai import LLMError
from groups.models import Group, GroupCharter, Membership
from groups.services import (
    charter_template,
    equity_comparison,
    generate_charter,
    parse_coach_members,
    recommend_rule,
)
from groups.views import guess_group_type

User = get_user_model()
VALID_CHARTER = "### Charte de « Coloc »\n\n1. On saisit chaque dépense le jour même.\n2. On rembourse en fin de mois."


class Fixtures:
    def setUp(self):
        self.admin = User.objects.create_user("ala", first_name="Ala")
        self.member = User.objects.create_user("yahya", first_name="Yahya")
        self.outsider = User.objects.create_user("imem")
        self.group = Group.objects.create(name="Coloc Lafayette", description="appart à Tunis")
        Membership.objects.create(group=self.group, user=self.admin, role=Membership.Role.ADMIN, weight=D("1"))
        Membership.objects.create(group=self.group, user=self.member, weight=D("2"))

    def url(self, name):
        return reverse(f"groups:{name}", kwargs={"gid": self.group.pk})


class CharterServiceTests(Fixtures, TestCase):
    @patch("core.ai.call_llm", return_value="```markdown\n" + VALID_CHARTER + "\n```")
    def test_llm_charter_used_when_valid(self, call_llm):
        result = generate_charter(self.group, "coloc", "dépenses communes réglées à la fin du mois")
        self.assertEqual(result["source"], "llm")
        self.assertEqual(result["content"], VALID_CHARTER)  # balises de code retirées
        prompt = call_llm.call_args.args[1]
        self.assertIn("dépenses communes réglées à la fin du mois", prompt)
        self.assertIn("Colocation", prompt)

    @patch("core.ai.call_llm", return_value="Voici une charte sympa sans aucune règle listée.")
    def test_text_without_rules_falls_back_to_template(self, _):
        self.assertEqual(generate_charter(self.group, "coloc", "")["source"], "template")

    @patch("core.ai.call_llm", side_effect=LLMError("indisponible"))
    def test_template_keeps_admin_key_rules_first(self, _):
        result = generate_charter(self.group, "voyage", "dépenses communes réglées à la fin du mois; pas d'achat > 50 TND sans accord")
        self.assertEqual(result["source"], "template")
        lines = result["content"].splitlines()
        self.assertEqual(lines[0], "### Charte de « Coloc Lafayette »")
        self.assertEqual(lines[2], "1. Dépenses communes réglées à la fin du mois.")
        self.assertEqual(lines[3], "2. Pas d'achat > 50 TND sans accord.")
        self.assertIn("soldés au retour", result["content"])  # règle propre au voyage

    def test_template_for_every_type(self):
        for group_type in GroupCharter.GroupType.values:
            with self.subTest(group_type=group_type):
                self.assertIn("1. ", charter_template(self.group, group_type, ""))

    def test_guess_group_type(self):
        self.assertEqual(guess_group_type(self.group), "coloc")
        self.assertEqual(guess_group_type(Group(name="Voyage Djerba")), "voyage")
        self.assertEqual(guess_group_type(Group(name="Les amis")), "autre")


class CharterViewTests(Fixtures, TestCase):
    def test_access_rules(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("charter")).status_code, 403)
        self.client.force_login(self.member)
        response = self.client.get(self.url("charter"))
        self.assertContains(response, "Pas encore de charte")
        self.assertNotContains(response, "Rédiger la charte avec l")
        self.assertEqual(self.client.get(self.url("charter_edit")).status_code, 403)
        self.assertEqual(self.client.post(self.url("charter_generate"), {"group_type": "coloc"}).status_code, 403)

    @patch("core.ai.call_llm", return_value=VALID_CHARTER)
    def test_generate_returns_proposal_without_saving(self, _):
        self.client.force_login(self.admin)
        data = self.client.post(self.url("charter_generate"), {"group_type": "coloc", "key_rules": "fin du mois"}).json()
        self.assertEqual((data["content"], data["source"]), (VALID_CHARTER, "llm"))
        self.assertFalse(GroupCharter.objects.exists())

    def test_generate_rejects_unknown_type(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url("charter_generate"), {"group_type": "pirate"}).status_code, 400)

    def test_admin_saves_and_members_read_it_safely(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url("charter_edit"), {
            "group_type": "coloc", "key_rules": "fin du mois",
            "content": VALID_CHARTER + "\n3. <script>alert(1)</script>", "ai_generated": "1",
        })
        self.assertRedirects(response, self.url("charter"))
        charter = GroupCharter.objects.get(group=self.group)
        self.assertEqual((charter.updated_by, charter.generated_by_ai), (self.admin, True))

        self.client.force_login(self.member)
        page = self.client.get(self.url("charter"))
        self.assertContains(page, "<li>On saisit chaque dépense le jour même.</li>", html=True)
        self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertContains(page, "rédigée avec l")

    def test_edit_prefills_guessed_type_and_rejects_empty(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url("charter_edit")).context["form"].initial["group_type"], "coloc")
        response = self.client.post(self.url("charter_edit"), {"group_type": "coloc", "content": "   "})
        self.assertIn("content", response.context["form"].errors)  # charte vide refusée
        self.assertFalse(GroupCharter.objects.exists())


class EquityServiceTests(TestCase):
    def test_report_example_90_with_weights_1_2_3(self):
        members = [{"name": "Étudiante", "weight": D("1")}, {"name": "Actif", "weight": D("2")},
                   {"name": "Senior", "weight": D("3")}]
        rows = equity_comparison(D("90"), members)["rows"]
        self.assertEqual([r["by_weight"] for r in rows], ["15.000", "30.000", "45.000"])
        self.assertEqual([r["equal"] for r in rows], ["30.000"] * 3)
        self.assertEqual(rows[0]["difference"], "-15.000")

    def test_shares_always_sum_to_amount(self):
        members = [{"name": str(i), "weight": D(w)} for i, w in enumerate(["1", "2", "2", "3.5", "0.25"])]
        rows = equity_comparison(D("100"), members)["rows"]
        self.assertEqual(sum(D(r["equal"]) for r in rows), D("100"))
        self.assertEqual(sum(D(r["by_weight"]) for r in rows), D("100"))

    def test_recommendation(self):
        same = [{"name": "a", "weight": D("2")}, {"name": "b", "weight": D("2")}]
        mixed = [{"name": "a", "weight": D("1")}, {"name": "b", "weight": D("2")}, {"name": "c", "weight": D("2")}]
        self.assertEqual(recommend_rule(same)["rule"], "equal")
        reco = recommend_rule(mixed)
        self.assertEqual(reco["rule"], "by_weight")
        self.assertIn("de 1 à 2", reco["reasons"][0])

    def test_scenario_validation(self):
        for raw in ("pas du json", "[]", json.dumps([{"name": "x", "weight": 0}]),
                    json.dumps([{"name": "x", "weight": 1000}]), json.dumps([{"name": "x", "weight": 1}] * 13)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_coach_members(raw)
        members = parse_coach_members(json.dumps([{"name": "x" * 80, "weight": "1.5"}, {"weight": 2}]))
        self.assertEqual((len(members[0]["name"]), members[1]["name"]), (40, "Personne 2"))


class EquityCoachViewTests(Fixtures, TestCase):
    scenario = json.dumps([{"name": "Étudiante", "weight": 1}, {"name": "Actif 1", "weight": 2},
                           {"name": "Actif 2", "weight": 2}])

    def test_page_lists_real_members(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url("equity"))
        members = response.context["coach_config"]["members"]
        self.assertCountEqual([(m["name"], m["weight"]) for m in members], [("Ala", 1.0), ("Yahya", 2.0)])
        self.assertContains(response, 'id="coach-data"')

    @patch("core.ai.call_llm", return_value="**Par poids** : l'étudiante paie **18 TND** au lieu de 30.")
    def test_ask_with_llm(self, call_llm):
        self.client.force_login(self.member)
        data = self.client.post(self.url("equity_ask"), {
            "amount": "75", "members": self.scenario,
            "question": "On est trois : une étudiante et deux actifs. Quelle règle est la plus adaptée ?",
        }).json()
        self.assertEqual(data["recommendation"]["rule"], "by_weight")
        self.assertEqual([r["by_weight"] for r in data["comparison"]["rows"]], ["15.000", "30.000", "30.000"])
        self.assertIn("<strong>Par poids</strong>", data["explanation_html"])
        prompt = call_llm.call_args.args[1]
        self.assertIn("une étudiante et deux actifs", prompt)
        self.assertIn("par poids 15 TND", prompt)  # montants à la française, jamais « 15.000 »

    @patch("core.ai.call_llm", side_effect=LLMError("indisponible"))
    def test_ask_without_llm_uses_template_table(self, _):
        self.client.force_login(self.member)
        data = self.client.post(self.url("equity_ask"), {"amount": "90", "members": self.scenario}).json()
        self.assertEqual(data["source"], "template")
        self.assertIn("<table>", data["explanation_html"])

    def test_ask_validation_and_access(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.url("equity_ask"), {"amount": "0", "members": self.scenario}).status_code, 400)
        self.assertEqual(self.client.post(self.url("equity_ask"), {"amount": "10", "members": "[]"}).status_code, 400)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(self.url("equity_ask"), {"amount": "10", "members": self.scenario}).status_code, 403)

    def test_only_admin_applies_rule(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.post(self.url("equity_apply"), {"rule": "by_weight"}).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(self.url("equity_apply"), {"rule": "by_weight"})
        self.group.refresh_from_db()
        self.assertEqual(self.group.default_split_rule, "by_weight")
        self.client.post(self.url("equity_apply"), {"rule": "n_importe_quoi"})
        self.group.refresh_from_db()
        self.assertEqual(self.group.default_split_rule, "by_weight")
