"""Client LLM (core/ai.py) : ordre des fournisseurs, secours, journalisation. Réseau simulé."""

from unittest.mock import patch

from django.test import TestCase, override_settings

from core import ai
from core.models import AIRequestLog


def fake_post(responses):
    """Simule _post : `responses` associe un fragment d'URL/modèle à une réponse ou une LLMError."""
    calls = []

    def _post(url, body, headers, timeout):
        model = body.get("model") or url.split("/models/")[1].split(":")[0]
        calls.append((model, timeout))
        result = responses[model]
        if isinstance(result, Exception):
            raise result
        return result

    return _post, calls


GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "réponse gemini"}]}}]}
GROQ_OK = {"choices": [{"message": {"content": "réponse groq"}}]}


@override_settings(LLM_API_KEY="AQ.gemini", GROQ_API_KEY="gsk_groq", GEMINI_MODELS=["g1", "g2"],
                   GROQ_MODELS=["q1"], LLM_TIMEOUT_SECONDS=40)
class CallLLMTests(TestCase):
    def test_first_gemini_model_answers(self):
        post, calls = fake_post({"g1": GEMINI_OK})
        with patch.object(ai, "_post", post):
            self.assertEqual(ai.call_llm("m3.test", "bonjour"), "réponse gemini")
        self.assertEqual(calls, [("g1", 40)])
        log = AIRequestLog.objects.get()
        self.assertTrue(log.success)
        self.assertEqual((log.module, log.feature, log.error_message), ("m3", "m3.test", ""))

    def test_falls_back_to_next_model_then_groq_with_shorter_timeout(self):
        post, calls = fake_post({
            "g1": ai.LLMError("HTTP 503: surcharge"),
            "g2": ai.LLMError("délai dépassé"),
            "q1": GROQ_OK,
        })
        with patch.object(ai, "_post", post):
            self.assertEqual(ai.call_llm("m3.test", "bonjour", module="settlements"), "réponse groq")
        self.assertEqual(calls, [("g1", 40), ("g2", 15), ("q1", 15)])
        log = AIRequestLog.objects.get()
        self.assertTrue(log.success)
        self.assertIn("gemini/g1", log.error_message)  # trace des échecs avant le secours

    def test_everything_fails(self):
        post, _ = fake_post({m: ai.LLMError("HTTP 500") for m in ("g1", "g2", "q1")})
        with patch.object(ai, "_post", post), self.assertRaises(ai.LLMError) as ctx:
            ai.call_llm("m3.test", "bonjour")
        self.assertIn("groq/q1", str(ctx.exception))
        self.assertFalse(AIRequestLog.objects.get().success)

    def test_malformed_or_empty_answers_trigger_fallback(self):
        post, _ = fake_post({"g1": {"oops": 1}, "g2": GEMINI_OK})
        with patch.object(ai, "_post", post):
            self.assertEqual(ai.call_llm("x", "y"), "réponse gemini")
        post, _ = fake_post({"g1": ai.LLMError("x"), "g2": ai.LLMError("x"),
                             "q1": {"choices": [{"message": {"content": ""}}]}})
        with patch.object(ai, "_post", post), self.assertRaises(ai.LLMError):
            ai.call_llm("x", "y")

    @override_settings(LLM_API_KEY='"gsk_dans_la_mauvaise_variable"', GROQ_API_KEY="")
    def test_groq_key_in_llm_api_key_is_detected(self):
        self.assertEqual(ai._providers(), [("groq", "gsk_dans_la_mauvaise_variable", ["q1"])])

    @override_settings(LLM_API_KEY="", GROQ_API_KEY="")
    def test_no_key(self):
        with self.assertRaises(ai.LLMError) as ctx:
            ai.call_llm("m3.test", "bonjour")
        self.assertIn("Aucune clé IA", str(ctx.exception))
        self.assertEqual(AIRequestLog.objects.get().error_message[:13], "Aucune clé IA")

    @override_settings(GEMINI_MODELS=[], GROQ_MODELS=[])
    def test_default_models_when_not_configured(self):
        providers = ai._providers()
        self.assertEqual(providers[0][2], ai.DEFAULT_GEMINI_MODELS)
        self.assertEqual(providers[1][2], ai.DEFAULT_GROQ_MODELS)
