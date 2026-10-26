"""Client LLM unique — Google Gemini via urllib (aucune dépendance externe)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from core.models import AIRequestLog

# Modèles Gemini disponibles (ordre de préférence, avec fallback en cas de surcharge 503)
_GEMINI_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest"]


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
        api_key = getattr(settings, "LLM_API_KEY", "").strip().strip('"').strip("'")
        if not api_key:
            raise LLMError(
                "LLM_API_KEY manquante. Définissez la variable d'environnement LLM_API_KEY."
            )

        timeout = getattr(settings, "LLM_TIMEOUT_SECONDS", 40)

        # Corps de la requête Gemini
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 1024,
            },
        }).encode("utf-8")

        raw = None
        last_exc = None
        for model_name in _GEMINI_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={urllib.parse.quote(api_key)}"
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
                last_exc = LLMError(f"Gemini HTTP {exc.code}: {detail}")
                if exc.code in (503, 404, 429):
                    continue  # essayer le modèle de secours
                raise last_exc from exc
            except urllib.error.URLError as exc:
                raise LLMError(f"Gemini réseau: {exc.reason}") from exc
            except TimeoutError as exc:
                raise LLMError("Gemini timeout — réessayez dans quelques secondes.") from exc

        if raw is None:
            if last_exc:
                raise last_exc
            raise LLMError("Aucun modèle Gemini n'a pu répondre.")

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
