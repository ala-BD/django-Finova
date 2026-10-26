"""Assistant : questions « comment faire » (get_help), routage FR/EN, liens internes sûrs."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from automation.help import HELP_TOPICS, build_help, help_markdown, topic_from_keywords
from automation.markdown import render_markdown
from automation.services import (
    _execute_assistant_function,
    _heuristic_function,
    _validate_arguments,
    answer_assistant_question,
)
from core.ai import LLMError
from groups.models import Group, Membership


class HeuristicRoutingTests(TestCase):
    """Routage sans LLM : les questions ne tombent plus toutes sur la même fonction."""

    def test_questions_reach_different_functions(self):
        cases = {
            "how to add a group": ("get_help", {"topic": "creer_groupe"}),
            "how to refund money": ("get_help", {"topic": "rembourser"}),
            "Comment ajouter une dépense ?": ("get_help", {"topic": "ajouter_depense"}),
            "comment inviter quelqu'un": ("get_help", {"topic": "inviter"}),
            "Combien je dois au total ?": ("get_balance", {}),
            "how much do I owe?": ("get_balance", {}),
            "Quelles sont mes dépenses ?": ("list_expenses", {"period": "month"}),
            "what did I spend": ("list_expenses", {"period": "month"}),
            "Résume mon mois": ("summarize_month", {}),
            "upcoming reminders": ("next_reminders", {}),
            "bonjour !": ("get_help", {"topic": ""}),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(_heuristic_function(question), expected)

    def test_topic_keywords(self):
        self.assertEqual(topic_from_keywords("comment créer un échéancier ?"), "echeancier")
        self.assertEqual(topic_from_keywords("rien à voir"), "")

    def test_unknown_topic_becomes_overview(self):
        self.assertEqual(_validate_arguments("get_help", {"topic": "drop table"}), {"topic": ""})


class HelpContentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("imem")
        self.other = get_user_model().objects.create_user("autre")
        self.mine = Group.objects.create(name="Coloc")
        Membership.objects.create(group=self.mine, user=self.user)
        self.theirs = Group.objects.create(name="Secret")
        Membership.objects.create(group=self.theirs, user=self.other)

    def test_links_use_only_the_users_groups(self):
        data = _execute_assistant_function(self.user, "get_help", {"topic": "rembourser"})
        urls = [link["url"] for link in data["liens"]]
        self.assertEqual(urls, [f"/groups/{self.mine.pk}/settlements/new/"])
        self.assertNotIn("Secret", str(data))

    def test_every_topic_has_steps_and_valid_internal_links(self):
        for topic in HELP_TOPICS:
            with self.subTest(topic=topic):
                data = build_help(topic, [self.mine])
                self.assertTrue(data["etapes"])
                for link in data["liens"]:
                    self.assertRegex(link["url"], r"^/[a-z0-9/_-]*/$")

    def test_overview_lists_capabilities(self):
        data = build_help("", [self.mine])
        self.assertIn("je_peux_repondre_sur", data)

    @patch("automation.services.call_llm", side_effect=LLMError("API indisponible"))
    def test_help_answer_without_llm_is_readable_markdown_with_links(self, _):
        message = answer_assistant_question(self.user, "comment rembourser quelqu'un ?")
        self.assertIn("### Rembourser quelqu'un", message.answer)
        self.assertNotIn("JSON", message.answer)
        html = render_markdown(message.answer)
        self.assertIn(f'<a href="/groups/{self.mine.pk}/settlements/new/">', html)
        self.assertIn("<ol>", html)

    def test_llm_routing_to_help(self):
        answers = iter(['```json\n{"function": "get_help", "arguments": {"topic": "creer_groupe"}}\n```',
                        "Pour créer un groupe : [Créer un groupe](/groups/new/)"])
        with patch("automation.services.call_llm", side_effect=lambda *a, **k: next(answers)) as call_llm:
            message = answer_assistant_question(self.user, "how do I create a group?")
        self.assertIn("/groups/new/", message.answer)
        answer_prompt = call_llm.call_args_list[1].args[1]
        self.assertIn("langue de la question", answer_prompt)
        self.assertIn("Ne parle jamais de JSON", answer_prompt)


class InternalLinksTests(TestCase):
    def test_internal_link_rendered(self):
        self.assertEqual(render_markdown("[Soldes](/groups/2/balances/)"),
                         '<p><a href="/groups/2/balances/">Soldes</a></p>')

    def test_external_and_dangerous_links_stay_text(self):
        for markdown in ("[x](javascript:alert(1))", "[x](https://evil.example/)", "[x](//evil.example/)",
                         '[x](/ok" onmouseover="alert(1))'):
            with self.subTest(markdown=markdown):
                self.assertNotIn("<a", render_markdown(markdown))

    def test_help_markdown_overview(self):
        text = help_markdown(build_help("", []))
        self.assertTrue(text.startswith("Je peux vous renseigner sur :"))
