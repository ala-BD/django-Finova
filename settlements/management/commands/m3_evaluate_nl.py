"""Évalue la déclaration en langage naturel sur ml/m3_nl_sentences.csv (30 phrases étiquetées).

    python manage.py m3_evaluate_nl            # analyse locale + LLM si LLM_API_KEY est définie

Mesure la précision par champ (bénéficiaire, montant, mode de paiement), la proportion de
phrases entièrement correctes et la latence ; écrit ml/m3_nl_metrics.json.
"""

import csv
import json
import statistics
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from settlements.nlp import PAYMENT_MODES, parse_repayment

MEMBERS = [
    (1, ["Ala", "Ben Salah", "ala"]),
    (2, ["Yahya", "Trabelsi", "yahya"]),
    (3, ["Linda", "Touil", "linda"]),
    (4, ["Zahra", "Mansouri", "zahra"]),
    (5, ["Wassim", "Jebali", "wassim"]),
]
IDS = {names[0]: member_id for member_id, names in MEMBERS}
MODE_LABELS = [label for _, label in PAYMENT_MODES]


def evaluate(rows, use_llm):
    hits = {"receiver": 0, "amount": 0, "mode": 0, "complete": 0}
    latencies, failures = [], []
    for row in rows:
        result = parse_repayment(row["sentence"], MEMBERS, use_llm=use_llm)
        latencies.append(result["latency_ms"])
        found_modes = [label for label in MODE_LABELS if label in result["note"]]
        ok = {
            "receiver": result["receiver"] == IDS[row["receiver"]],
            "amount": result["amount"] == Decimal(row["amount"]).quantize(Decimal("0.001")),
            "mode": found_modes == ([row["mode"]] if row["mode"] else []),
        }
        for key, value in ok.items():
            hits[key] += value
        hits["complete"] += all(ok.values())
        if not all(ok.values()):
            failures.append({"sentence": row["sentence"], "result": {
                "receiver": result["receiver"], "amount": str(result["amount"]), "note": result["note"]}})
    n = len(rows)
    return {
        "sentences": n,
        "accuracy": {key: round(value / n, 3) for key, value in hits.items()},
        "latency_ms": {"mean": round(statistics.mean(latencies), 1),
                       "p95": sorted(latencies)[int(0.95 * (n - 1))]},
        "failures": failures,
    }


class Command(BaseCommand):
    help = "Mesure la précision de la déclaration de remboursement en langage naturel."

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR) / "ml"
        with open(base / "m3_nl_sentences.csv", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        metrics = {"rules": evaluate(rows, use_llm=False)}
        if settings.LLM_API_KEY:
            metrics["llm"] = evaluate(rows, use_llm=True)
        else:
            metrics["llm"] = "non mesuré : LLM_API_KEY absente"

        (base / "m3_nl_metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        for mode, result in metrics.items():
            if isinstance(result, str):
                self.stdout.write(f"{mode} : {result}")
                continue
            acc = result["accuracy"]
            self.stdout.write(
                f"{mode} : bénéficiaire {acc['receiver']:.0%} · montant {acc['amount']:.0%} · "
                f"mode {acc['mode']:.0%} · phrase complète {acc['complete']:.0%} · "
                f"latence moy. {result['latency_ms']['mean']} ms (p95 {result['latency_ms']['p95']} ms)"
            )
            for failure in result["failures"]:
                self.stdout.write(f"  ✗ {failure['sentence']} → {failure['result']}")
