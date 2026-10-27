"""M2 · IA : catégorie suggérée depuis le titre et saisie en langage naturel. Aucun appel réseau (LLM simulé)."""

import json
from datetime import date, timedelta
from decimal import Decimal as D
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from budgets.models import Category
from core.ai import LLMError
from groups.models import Group, Membership

from .ml import label_from_name, match_group_category, predict_category
from .models import Expense
from .nlp import local_title, parse_expense
from .services import predict_category as service_predict

User = get_user_model()
TODAY = date(2026, 10, 27)
MEMBERS = [(1, ["Ala", "ala"]), (2, ["Yahya", "yahya"]), (3, ["Linda", "linda"])]


class CategoryPredictionTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Coloc", currency="TND")

    def test_realistic_titles(self):
        for title, label in [("pizza du vendredi", "alimentation"), ("taxi aéroport", "transport"),
                             ("loyer octobre", "logement"), ("facture STEG", "logement"),
                             ("pharmacie de garde", "sante"), ("cinéma samedi", "loisirs"),
                             ("chaussures Zara", "shopping"), ("recharge Ooredoo", "factures")]:
            with self.subTest(title=title):
                self.assertEqual(predict_category(title)[0], label)

    def test_never_falls_back_to_a_random_category(self):
        Category.objects.create(group=self.group, name="Alimentation")
        result = service_predict("taxi aéroport", self.group)
        self.assertEqual(result["label"], "transport")
        self.assertIsNone(result["category_id"])  # avant : la première catégorie venue (Alimentation)
        self.assertEqual((result["category_name"], result["exists"]), ("Transport", False))

    def test_matches_aliases_without_accents_and_only_in_the_group(self):
        other = Group.objects.create(name="Autre")
        Category.objects.create(group=other, name="Santé")
        self.assertIsNone(match_group_category("sante", self.group))
        mine = Category.objects.create(group=self.group, name="pharmacie")
        self.assertEqual(match_group_category("sante", self.group), mine)
        resto = Category.objects.create(group=self.group, name="Restaurant")
        self.assertEqual(service_predict("pizza du vendredi", self.group)["category_id"], resto.id)

    def test_label_from_name(self):
        self.assertEqual(label_from_name("Médical"), "sante")
        self.assertEqual(label_from_name("factures"), "factures")
        self.assertIsNone(label_from_name("Bitcoin"))

    @patch("expenses.ml.get_model", return_value=None)
    def test_keyword_fallback_without_model(self, _):
        label, confidence, source = predict_category("courses carrefour")
        self.assertEqual((label, source), ("alimentation", "mots-cles"))
        self.assertGreater(confidence, 0)

    def test_empty_title(self):
        self.assertEqual(predict_category("   ")[0:2], ("autre", 0.0))


class LocalParsingTests(TestCase):
    def test_title_cleanup(self):
        self.assertEqual(local_title("j'ai paye 45 dinars le resto hier avec sami"), "Resto")
        self.assertEqual(local_title("85 dinars courses carrefour hier"), "Courses carrefour")

    def test_rules_only(self):
        result = parse_expense("Pizza 42,500 dinars avant-hier avec Yahya et Sami", MEMBERS,
                               use_llm=False, today=TODAY)
        self.assertEqual(result["amount"], D("42.500"))
        self.assertEqual(result["expense_date"], TODAY - timedelta(days=2))
        self.assertEqual(result["category"], "alimentation")
        self.assertEqual((result["participants"], result["unknown_participants"]), ([2], ["sami"]))
        self.assertEqual((result["source"], result["missing"]), ("regles", []))


class LLMParsingTests(TestCase):
    def llm(self, payload):
        return patch("expenses.nlp.call_llm", return_value=json.dumps(payload) if isinstance(payload, dict) else payload)

    def test_valid_json_is_used(self):
        answer = {"title": "dîner libanais", "amount": 96, "date": "2026-10-26",
                  "category": "alimentation", "participants": ["Linda", "Inconnu"]}
        with self.llm("```json\n" + json.dumps(answer) + "\n```") as call_llm:
            result = parse_expense("Dîner libanais 96 dinars hier avec Linda", MEMBERS, today=TODAY)
        self.assertEqual(result["title"], "Dîner libanais")
        self.assertEqual((result["amount"], result["expense_date"]), (D("96.000"), date(2026, 10, 26)))
        self.assertEqual((result["participants"], result["unknown_participants"]), ([3], ["Inconnu"]))
        self.assertEqual(result["source"], "llm")
        prompt = call_llm.call_args.args[1]
        self.assertIn("2026-10-27", prompt)
        self.assertIn("alimentation, transport", prompt)

    def test_invalid_values_are_rejected_then_completed_by_rules(self):
        answer = {"title": "", "amount": -5, "date": "2027-01-01", "category": "crypto", "participants": []}
        with self.llm(answer):
            result = parse_expense("Taxi 32 DT hier", MEMBERS, today=TODAY)
        self.assertEqual(result["amount"], D("32.000"))
        self.assertEqual(result["expense_date"], TODAY - timedelta(days=1))  # date future refusée
        self.assertEqual(result["category"], "transport")
        self.assertEqual(result["source"], "llm+regles")

    def test_millimes_written_with_currency_win_over_llm(self):
        answer = {"title": "Pharmacie", "amount": 18500, "date": "2026-10-27", "category": "sante", "participants": []}
        with self.llm(answer):
            self.assertEqual(parse_expense("Pharmacie 18.500 DT", MEMBERS, today=TODAY)["amount"], D("18.500"))

    def test_category_given_as_display_name(self):
        answer = {"title": "Dentiste", "amount": 60, "date": "2026-10-27", "category": "Santé", "participants": []}
        with self.llm(answer):
            self.assertEqual(parse_expense("dentiste 60 dinars", MEMBERS, today=TODAY)["category"], "sante")

    def test_garbage_and_errors_fall_back_to_rules(self):
        for mock in (self.llm("désolé, je ne sais pas"), self.llm('{"participants": "Ala"}'),
                     patch("expenses.nlp.call_llm", side_effect=LLMError("quota"))):
            with self.subTest(), mock:
                result = parse_expense("Courses 85 dinars", MEMBERS, today=TODAY)
                self.assertEqual((result["source"], result["amount"]), ("regles", D("85.000")))


class ExpenseAIViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("yahya", first_name="Yahya")
        self.friend = User.objects.create_user("linda", first_name="Linda")
        self.outsider = User.objects.create_user("intrus")
        self.group = Group.objects.create(name="Voyage", currency="TND")
        self.membership = Membership.objects.create(group=self.group, user=self.user, role="admin")
        Membership.objects.create(group=self.group, user=self.friend)
        self.client.force_login(self.user)

    def url(self, name):
        return reverse(f"expenses:{name}", kwargs={"gid": self.group.id})

    def test_categorize_suggests_missing_category(self):
        data = self.client.get(self.url("categorize"), {"title": "pharmacie de garde"}).json()
        self.assertEqual((data["label"], data["category_id"], data["exists"]), ("sante", None, False))
        self.assertEqual(data["category_name"], "Santé")
        category = Category.objects.create(group=self.group, name="Santé")
        self.assertEqual(self.client.get(self.url("categorize"), {"title": "pharmacie"}).json()["category_id"], category.id)

    def test_categorize_requires_membership(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("categorize"), {"title": "taxi"}).status_code, 403)

    def payload(self, **extra):
        return {"title": "Pharmacie", "amount": "18.500", "currency": "TND",
                "expense_date": timezone.localdate().isoformat(), "payer": self.membership.id,
                "split_rule": "equal", "tax_amount": "0", "tip_amount": "0", **extra}

    def test_suggested_category_is_created_on_save(self):
        self.client.post(self.url("create"), self.payload(new_category_name="santé"))
        expense = Expense.objects.get(title="Pharmacie")
        self.assertEqual((expense.category.name, expense.category.group), ("Santé", self.group))
        self.client.post(self.url("create"), self.payload(title="Doliprane", new_category_name="Santé"))
        self.assertEqual(Category.objects.filter(group=self.group).count(), 1)  # réutilisée, pas dupliquée

    def test_manual_category_wins_and_unknown_name_is_refused(self):
        chosen = Category.objects.create(group=self.group, name="Divers")
        self.client.post(self.url("create"), self.payload(category=chosen.id, new_category_name="Santé"))
        self.assertEqual(Expense.objects.get().category, chosen)
        response = self.client.post(self.url("create"), self.payload(title="X", new_category_name="Bitcoin"))
        self.assertIn("category", response.context["form"].errors)

    @patch("expenses.nlp.call_llm", return_value=json.dumps({
        "title": "Resto", "amount": 45, "date": "2000-01-01", "category": "alimentation", "participants": ["Linda", "Sami"]}))
    def test_nlp_extract_returns_validated_fields(self, _):
        data = self.client.post(self.url("nlp_extract"), {"phrase": "45 dinars resto avec Linda et Sami"}).json()
        self.assertTrue(data["success"])
        self.assertEqual((data["amount"], data["currency"], data["title"]), ("45.000", "TND", "Resto"))
        self.assertIsNone(data["expense_date"])  # date trop ancienne refusée, le formulaire garde aujourd'hui
        self.assertEqual((data["participants"], data["unknown_participants"]), (["Linda"], ["Sami"]))
        self.assertEqual((data["category_name"], data["exists"]), ("Alimentation", False))

    @patch("expenses.nlp.call_llm", side_effect=LLMError("indisponible"))
    def test_nlp_extract_errors(self, _):
        response = self.client.post(self.url("nlp_extract"), {"phrase": "   "})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url("nlp_extract"), {"phrase": "?!"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("formulaire", response.json()["error"])

    def test_form_page_loads_ai_script_and_hidden_field(self):
        page = self.client.get(self.url("create"))
        self.assertContains(page, "js/expense_ai.js")
        self.assertContains(page, 'name="new_category_name"')
        self.assertContains(page, 'id="ai-suggestion"')
