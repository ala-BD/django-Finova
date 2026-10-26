"""Client LLM unique — Google Gemini, avec Groq en secours (urllib, aucune dépendance externe).

Ordre d'essai pour chaque appel :
  1. Gemini (clé LLM_API_KEY), modèles GEMINI_MODELS dans l'ordre ;
  2. Groq (clé GROQ_API_KEY, API compatible OpenAI), modèles GROQ_MODELS dans l'ordre.
Une clé Groq (préfixe « gsk_ ») placée par erreur dans LLM_API_KEY est reconnue et utilisée
comme clé Groq. On passe au modèle ou au fournisseur suivant en cas de surcharge, quota,
modèle retiré, erreur réseau ou délai dépassé ; seule l'absence totale de réponse lève LLMError.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from django.conf import settings

from core.models import AIRequestLog

DEFAULT_GEMINI_MODELS = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite"]
DEFAULT_GROQ_MODELS = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
BACKUP_TIMEOUT_SECONDS = 15  # un modèle de secours ne doit pas faire attendre aussi longtemps


class LLMError(Exception):
    """Erreur d'appel au modèle de langage."""


def _clean(key) -> str:
    return str(key or "").strip().strip('"').strip("'")


def _providers() -> list[tuple[str, str, list[str]]]:
    """[(fournisseur, clé, modèles)] dans l'ordre d'essai, d'après les réglages."""
    llm_key = _clean(getattr(settings, "LLM_API_KEY", ""))
    groq_key = _clean(getattr(settings, "GROQ_API_KEY", ""))
    if llm_key.startswith("gsk_"):  # clé Groq dans LLM_API_KEY
        groq_key, llm_key = groq_key or llm_key, ""
    providers = []
    if llm_key:
        providers.append(("gemini", llm_key, getattr(settings, "GEMINI_MODELS", None) or DEFAULT_GEMINI_MODELS))
    if groq_key:
        providers.append(("groq", groq_key, getattr(settings, "GROQ_MODELS", None) or DEFAULT_GROQ_MODELS))
    return providers


def _post(url, body, headers, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "finova", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:150]
        raise LLMError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"réseau : {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMError(f"délai dépassé ({timeout} s)") from exc


def _ask_gemini(key, model, prompt, timeout) -> str:
    raw = _post(
        GEMINI_URL.format(model=model),
        {"contents": [{"parts": [{"text": prompt}]}],
         "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1024}},
        {"x-goog-api-key": key},
        timeout,
    )
    try:
        return raw["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"réponse inattendue : {str(raw)[:150]}") from exc


def _ask_groq(key, model, prompt, timeout) -> str:
    raw = _post(
        GROQ_URL,
        {"model": model, "messages": [{"role": "user", "content": prompt}],
         "temperature": 0.4, "max_tokens": 1024},
        {"Authorization": f"Bearer {key}"},
        timeout,
    )
    try:
        text = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"réponse inattendue : {str(raw)[:150]}") from exc
    if not text:
        raise LLMError("réponse vide")
    return text


ASK = {"gemini": _ask_gemini, "groq": _ask_groq}


def call_llm(feature: str, prompt: str, schema=None, module: str = "", user=None) -> str:
    """
    Point d'entrée unique pour tous les modules.

    - Clés lues dans settings.LLM_API_KEY (Gemini) et settings.GROQ_API_KEY (secours)
    - Délai : settings.LLM_TIMEOUT_SECONDS pour le premier essai, 15 s au plus ensuite
    - Journalise chaque appel dans AIRequestLog (latence totale, succès, dernière erreur)
    - Dans les tests : mocker cette fonction, aucun appel réseau réel

    Retourne : la réponse textuelle du LLM (str)
    Lève    : LLMError si aucun fournisseur ni modèle n'a répondu
    """
    started = time.perf_counter()
    success = False
    errors: list[str] = []
    try:
        providers = _providers()
        if not providers:
            raise LLMError(
                "Aucune clé IA : définissez LLM_API_KEY (Gemini) et/ou GROQ_API_KEY dans l'environnement."
            )
        timeout = int(getattr(settings, "LLM_TIMEOUT_SECONDS", 20))
        for provider, key, models in providers:
            for model in models:
                try:
                    text = ASK[provider](key, model, prompt, timeout)
                    success = True
                    return text
                except LLMError as exc:
                    errors.append(f"{provider}/{model} : {exc}")
                    timeout = min(timeout, BACKUP_TIMEOUT_SECONDS)
        raise LLMError("Aucun modèle n'a répondu. " + " | ".join(errors))
    except LLMError as exc:
        if not errors:
            errors.append(str(exc))
        raise
    finally:
        AIRequestLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            module=module or feature.split(".", 1)[0],
            feature=feature,
            latency_ms=int((time.perf_counter() - started) * 1000),
            success=success,
            # Après un secours réussi, on garde la trace des modèles qui ont échoué avant
            error_message=" | ".join(errors)[:255],
        )
