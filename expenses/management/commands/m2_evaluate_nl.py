"""Évalue la saisie de dépense en langage naturel sur ml/m2_nl_sentences.csv (40 phrases étiquetées).

    python manage.py m2_evaluate_nl            # analyse locale + LLM si une clé IA est définie

Mesure la précision par champ (montant, catégorie, date), la proportion de phrases entièrement
correctes et la latence du LLM lue dans AIRequestLog (objectif < 6 s) ;
écrit ml/m2_nl_metrics.json. Objectif spec : montant correct >= 95 %.
"""

import csv
import json
import statistics
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import AIRequestLog
from expenses.nlp import parse_expense

MEMBERS = [
    (1, ["Ala", "ala"]),
    (2, ["Yahya", "yahya"]),
    (3, ["Imem", "imem"]),
    (4, ["Linda", "linda"]),
    (5, ["Zahra", "zahra"]),
    (6, ["Wassim", "wassim"]),
]


def percentile(values, ratio):
    return sorted(values)[int(ratio * (len(values) - 1))] if values else None


def evaluate(rows, use_llm, today):
    hits = {"amount": 0, "category": 0, "date": 0, "complete": 0}
    failures = []
    for row in rows:
        result = parse_expense(row["sentence"], MEMBERS, use_llm=use_llm, today=today)
        ok = {
            "amount": result["amount"] == Decimal(row["amount"]).quantize(Decimal("0.001")),
            "category": result["category"] == row["category"],
            # Sans date dans la phrase, « aujourd'hui » (LLM) ou rien (règles → le formulaire garde aujourd'hui)
            "date": (result["expense_date"] or today) == today - timedelta(days=int(row["days_ago"])),
        }
        for key, value in ok.items():
            hits[key] += value
        hits["complete"] += all(ok.values())
        if not all(ok.values()):
            failures.append({"sentence": row["sentence"], "result": {
                "title": result["title"], "amount": str(result["amount"]), "category": result["category"],
                "date": str(result["expense_date"]), "source": result["source"]}})
    n = len(rows)
    return {"sentences": n, "accuracy": {key: round(value / n, 3) for key, value in hits.items()},
            "failures": failures}


class Command(BaseCommand):
    help = "Mesure la précision de la saisie de dépense en langage naturel (M2)."

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR) / "ml"
        with open(base / "m2_nl_sentences.csv", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        today = date.today()

        metrics = {"rules": evaluate(rows, use_llm=False, today=today)}
        if settings.LLM_API_KEY or getattr(settings, "GROQ_API_KEY", ""):
            first_log = AIRequestLog.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
            metrics["llm"] = evaluate(rows, use_llm=True, today=today)
            logs = AIRequestLog.objects.filter(pk__gt=first_log, feature="m2.parse_expense")
            latencies = [log.latency_ms for log in logs if log.latency_ms is not None]
            metrics["llm"]["ai_request_log"] = {
                "calls": len(latencies),
                "success_rate": round(logs.filter(success=True).count() / len(latencies), 3) if latencies else None,
                "latency_ms_mean": round(statistics.mean(latencies), 1) if latencies else None,
                "latency_ms_p95": percentile(latencies, 0.95),
                "target_ms": 6000,
            }
        else:
            metrics["llm"] = "non mesuré : aucune clé IA (LLM_API_KEY / GROQ_API_KEY)"

        (base / "m2_nl_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        for mode, result in metrics.items():
            if isinstance(result, str):
                self.stdout.write(f"{mode} : {result}")
                continue
            acc = result["accuracy"]
            line = (f"{mode} : montant {acc['amount']:.0%} · catégorie {acc['category']:.0%} · "
                    f"date {acc['date']:.0%} · phrase complète {acc['complete']:.0%}")
            log = result.get("ai_request_log")
            if log and log["calls"]:
                line += (f" · latence LLM (AIRequestLog) moy. {log['latency_ms_mean']} ms, "
                         f"p95 {log['latency_ms_p95']} ms, succès {log['success_rate']:.0%}")
            self.stdout.write(line)
            for failure in result["failures"]:
                self.stdout.write(f"  ✗ {failure['sentence']} → {failure['result']}")
