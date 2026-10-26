"""Interface de l'assistant : rendu Markdown sûr, réponse JSON, historique chronologique."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from automation.markdown import render_markdown
from automation.models import ChatMessage


class MarkdownRenderingTests(TestCase):
    def test_inline_formatting(self):
        self.assertEqual(
            render_markdown("Vous devez **343,830 TND** à *Ala* (`get_balance`)."),
            "<p>Vous devez <strong>343,830 TND</strong> à <em>Ala</em> (<code>get_balance</code>).</p>",
        )

    def test_lists_headings_and_paragraphs(self):
        html = render_markdown("### Vos dépenses\n\n* **Pizza** : 47,300\n* Loyer : 900\n\n1. un\n2. deux\nFin")
        self.assertEqual(
            html,
            "<h5>Vos dépenses</h5><ul><li><strong>Pizza</strong> : 47,300</li><li>Loyer : 900</li></ul>"
            "<ol><li>un</li><li>deux</li></ol><p>Fin</p>",
        )

    def test_table(self):
        html = render_markdown("| Titre | Montant |\n|---|---:|\n| Loyer | 900 |\n| Pizza | 47,3 |")
        self.assertIn('<th scope="col">Titre</th>', html)
        self.assertIn("<tr><td>Pizza</td><td>47,3</td></tr>", html)

    def test_html_and_scripts_are_escaped(self):
        for payload in ('<script>alert(1)</script>', '<img src=x onerror=alert(1)>',
                        '**<b onmouseover="x">gras</b>**', '[lien](javascript:alert(1))'):
            with self.subTest(payload=payload):
                html = render_markdown(payload)
                self.assertNotIn("<script", html)
                self.assertNotIn("<img", html)
                self.assertNotIn("<b ", html)
                self.assertNotIn("<a", html)
        self.assertIn("&lt;script&gt;", render_markdown("<script>alert(1)</script>"))

    def test_code_span_is_not_formatted(self):
        self.assertEqual(render_markdown("`**pas gras**`"), "<p><code>**pas gras**</code></p>")

    def test_line_breaks_inside_paragraph(self):
        self.assertEqual(render_markdown("ligne 1\nligne 2"), "<p>ligne 1<br>ligne 2</p>")


class AssistantPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("imem", "imem@example.com", first_name="Imem")
        self.client.force_login(self.user)

    def test_empty_state_and_suggestions(self):
        response = self.client.get(reverse("automation:assistant"))
        self.assertContains(response, "Bonjour Imem")
        self.assertContains(response, 'class="asst-chip"', count=4)
        self.assertContains(response, 'role="log"')

    def test_history_is_chronological_and_rendered_as_markdown(self):
        old = ChatMessage.objects.create(user=self.user, question="Première ?", answer="**Ancienne** réponse")
        ChatMessage.objects.create(user=self.user, question="Deuxième ?", answer="<script>x</script>")
        ChatMessage.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=1))
        html = self.client.get(reverse("automation:assistant")).content.decode()
        self.assertLess(html.index("Première ?"), html.index("Deuxième ?"))
        self.assertIn("<strong>Ancienne</strong>", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)

    def test_chat_returns_rendered_answer(self):
        message = ChatMessage(user=self.user, question="q", answer="* **A** : 1")
        message.created_at = timezone.now()
        with patch("automation.views.answer_assistant_question", return_value=message):
            response = self.client.post(reverse("automation:assistant_chat"), {"question": "q"})
        data = response.json()
        self.assertEqual(data["answer_html"], "<ul><li><strong>A</strong> : 1</li></ul>")
        self.assertIn("created_at", data)

    def test_missing_module_gives_readable_error(self):
        with patch("automation.views.answer_assistant_question", side_effect=NotImplementedError("budgets.x")):
            response = self.client.post(reverse("automation:assistant_chat"), {"question": "mon budget ?"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("pas encore disponible", response.json()["error"])
