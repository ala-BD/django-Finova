"""Client LLM unique. Yahya (M2) complète l'implémentation réelle."""

from __future__ import annotations

import time

from django.conf import settings

from core.models import AIRequestLog


class LLMError(Exception):
    """Erreur d'appel au modèle de langage."""


def call_llm(feature: str, prompt: str, schema=None, module: str = "", user=None):
    """
    Point d'entrée unique pour tous les modules.

    - Clé lue dans LLM_API_KEY
    - Timeout, journal AIRequestLog
    - Aucun appel réseau dans les tests : mocker cette fonction
    """
    started = time.perf_counter()
    success = False
    error_message = ""
    try:
        if not settings.LLM_API_KEY:
            raise LLMError("LLM_API_KEY manquante. Définir la variable d'environnement.")
        raise LLMError(
            "call_llm n'est pas encore branché. Voir core/ai.py (Yahya / client IA commun)."
        )
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
