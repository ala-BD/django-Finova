"""Client LLM unique et robuste pour Finova V2 (Yahya / M2 - client IA commun).

Gère :
- Lecture de LLM_API_KEY
- Timeout paramétrable (LLM_TIMEOUT_SECONDS)
- Nouvelle tentative (retry) en cas d'échec
- Journalisation systématique dans AIRequestLog (module, fonction, latence, succès)
- Repli intelligent (fallback local) pour garantir que la démo live ne dépend jamais d'une panne réseau
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

from core.models import AIRequestLog


class LLMError(Exception):
    """Erreur d'appel au modèle de langage."""


def _call_external_api(prompt: str, api_key: str, timeout: int, schema: Optional[dict] = None) -> str:
    """Appel externe sécurisé supportant les API OpenAI ou Gemini."""
    if api_key.startswith("AIza"):
        # Google Gemini API
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        }
        if schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"
    else:
        # OpenAI API (ou compatible)
        url = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Tu es l'assistant financier Finova V2. Réponds en JSON structuré si demandé."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if schema:
            payload["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if not api_key.startswith("AIza"):
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if api_key.startswith("AIza"):
        return data["candidates"][0]["content"]["parts"][0]["text"]
    return data["choices"][0]["message"]["content"]


def _fallback_expense_nlp(prompt: str) -> dict[str, Any]:
    """
    Repli local haute fiabilité pour la saisie en langage naturel (M2).
    Permet une démo live fluide même hors-ligne ou sans clé d'API.
    Exemple: « j'ai payé 45 dinars le resto hier avec Sami et Mariem »
    """
    text = prompt.strip()

    # 1. Extraction du montant
    amount = 0.0
    amount_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:dinars?|dt|tnd|eur|€|\$)?", text, re.IGNORECASE)
    if amount_match:
        amount_str = amount_match.group(1).replace(",", ".")
        try:
            amount = float(amount_str)
        except ValueError:
            amount = 0.0

    # 2. Extraction de la date
    expense_date = timezone.localdate()
    lower_text = text.lower()
    if "hier" in lower_text:
        expense_date = expense_date - timedelta(days=1)
    elif "avant-hier" in lower_text or "avant hier" in lower_text:
        expense_date = expense_date - timedelta(days=2)

    # 3. Extraction de la devise
    currency = "TND"
    if "eur" in lower_text or "€" in lower_text:
        currency = "EUR"
    elif "usd" in lower_text or "$" in lower_text:
        currency = "USD"

    # 4. Extraction du titre épuré
    # Retirer les mots introductifs courants
    clean_title = re.sub(
        r"^(?:j'ai\s+payé|payé|dépensé|achat\s+de|frais\s+de)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    # Si le titre commence par le montant, nettoyer
    clean_title = re.sub(r"^\d+(?:[.,]\d+)?\s*(?:dinars?|dt|tnd)?\s*(?:pour|au|le|chez)?\s*", "", clean_title, flags=re.IGNORECASE).strip()
    if not clean_title:
        clean_title = "Dépense"
    else:
        clean_title = clean_title[0].upper() + clean_title[1:]

    # 5. Extraction des participants mentionnés (avec ...)
    participants = []
    with_match = re.search(r"avec\s+([^.,;]+)", text, re.IGNORECASE)
    if with_match:
        raw_parts = with_match.group(1)
        names = re.split(r"\s+et\s+|,\s*", raw_parts)
        participants = [n.strip().capitalize() for n in names if n.strip()]

    # 6. Catégorisation locale via le classifieur M2 si disponible
    category_name = "Autre"
    try:
        from expenses.ml import predict_category_label
        category_name = predict_category_label(clean_title)
    except Exception:
        pass

    return {
        "title": clean_title,
        "amount": amount,
        "currency": currency,
        "expense_date": expense_date.isoformat(),
        "category": category_name,
        "participants": participants,
    }


def call_llm(feature: str, prompt: str, schema=None, module: str = "", user=None) -> Any:
    """
    Point d'entrée unique pour tous les modules de Finova V2.

    - Lit LLM_API_KEY et LLM_TIMEOUT_SECONDS
    - Tente l'appel externe avec 1 retry
    - En cas d'absence de clé ou d'erreur réseau, bascule sur le repli local
    - Enregistre systématiquement la requête dans AIRequestLog
    """
    started = time.perf_counter()
    success = False
    error_message = ""
    result: Any = None

    api_key = getattr(settings, "LLM_API_KEY", "")
    timeout = int(getattr(settings, "LLM_TIMEOUT_SECONDS", 20))

    if api_key:
        # Tentative d'appel avec un retry
        for attempt in range(2):
            try:
                raw_response = _call_external_api(prompt, api_key, timeout, schema=schema)
                if schema or "json" in prompt.lower():
                    # Nettoyage d'éventuels blocs markdown ```json ... ```
                    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_response.strip())
                    cleaned = re.sub(r"\s*```$", "", cleaned)
                    result = json.loads(cleaned)
                else:
                    result = raw_response
                success = True
                break
            except Exception as exc:
                error_message = str(exc)
                if attempt == 0:
                    time.sleep(0.5)
                    continue

    # Si l'appel externe n'a pas réussi ou pas de clé : repli local non-bloquant
    if not success:
        if "nlp_expense" in feature or "m2" in feature or "depense" in feature.lower():
            result = _fallback_expense_nlp(prompt)
            success = True
            error_message = "Repli local M2 activé (sans clé ou réseau)"
        elif schema:
            result = {}
            error_message = error_message or "Repli local actif"
        else:
            result = "Réponse générée par le repli local Finova."

    latency_ms = int((time.perf_counter() - started) * 1000)

    # Journalisation dans AIRequestLog
    try:
        AIRequestLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            module=module or feature.split(".", 1)[0],
            feature=feature,
            latency_ms=latency_ms,
            success=success,
            error_message=error_message[:255],
        )
    except Exception:
        pass

    return result
