"""Client LLM unique — Google Gemini via urllib (aucune dépendance externe)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from core.models import AIRequestLog

# Modèle Gemini utilisé pour toutes les générations
_GEMINI_MODEL = "gemini-2.0-flash"
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{_GEMINI_MODEL}:generateContent"
)


class LLMError(Exception):
    """Erreur d'appel au modèle de langage."""


def call_llm(feature: str, prompt: str, schema=None, module: str = "", user=None) -> str:
    """
    Point d'entrée unique pour tous les modules.

    - Clé API lue dans settings.LLM_API_KEY (variable d'env LLM_API_KEY)
    - Timeout configuré via settings.LLM_TIMEOUT_SECONDS (défaut 20s)
    - Journalise chaque appel dans AIRequestLog (latence, succès, erreur)
    - Dans les tests : mocker cette fonction, aucun appel réseau réel

    Retourne : la réponse textuelle du LLM (str)
    Lève    : LLMError en cas d'échec
    """
    started = time.perf_counter()
    success = False
    error_message = ""
    response_text = ""

    try:
        api_key = getattr(settings, "LLM_API_KEY", "")
        if not api_key:
            raise LLMError(
                "LLM_API_KEY manquante. Définissez la variable d'environnement LLM_API_KEY."
            )

        timeout = getattr(settings, "LLM_TIMEOUT_SECONDS", 20)

        # Corps de la requête Gemini
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 1024,
            },
        }).encode("utf-8")

        url = f"{_GEMINI_URL}?key={urllib.parse.quote(api_key)}"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise LLMError(f"Gemini HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"Gemini réseau: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMError("Gemini timeout — réessayez dans quelques secondes.") from exc

        try:
            response_text = raw["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Réponse Gemini inattendue : {str(raw)[:200]}") from exc

        success = True
        return response_text

    except LLMError as exc:
        error_message = str(exc)
        raise
    finally:
        AIRequestLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            module=module or feature.split(".", 1)[0],
            feature=feature,
            latency_ms=int((time.perf_counter() - started) * 1000),
            success=success,
            error_message=error_message[:255],
        )
