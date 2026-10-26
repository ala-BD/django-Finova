"""
Métriques M5 pour la soutenance : précision et rappel de la détection, temps de réponse.

Protocole (rapport, section 10) : 500 dépenses générées, 30 anomalies (montants ×5 à ×10)
et 20 doublons (même montant, date ± 1 jour, libellé proche) injectés.

    python manage.py analytics_evaluate --seeds 1 2 3 4 5
"""

import json
import statistics
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from analytics.demo import evaluation_dataset
from analytics.detection import detect_anomalies, detect_duplicates, precision_recall
from core.models import AIRequestLog


class Command(BaseCommand):
    help = "Évalue la détection d'anomalies et de doublons sur des jeux générés (M5)."

    def add_arguments(self, parser):
        parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3, 4, 5])
        parser.add_argument(
            "--output",
            default=str(Path(settings.BASE_DIR) / "ml" / "m5_metrics.json"),
            help="Fichier JSON des métriques (versionné dans ml/).",
        )

    def handle(self, *args, seeds, output, **options):
        runs = []
        for seed in seeds:
            dataset = evaluation_dataset(today=date(2026, 10, 26), seed=seed)
            started = time.perf_counter()
            anomalies = {d.record.id for d in detect_anomalies(dataset.records)}
            anomaly_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            duplicates = {d.record.id for d in detect_duplicates(dataset.records)}
            duplicate_ms = (time.perf_counter() - started) * 1000
            run = {
                "seed": seed,
                "expenses": len(dataset.records),
                "anomalies": precision_recall(anomalies, dataset.anomaly_ids),
                "duplicates": precision_recall(duplicates, dataset.duplicate_ids),
                "anomaly_ms": round(anomaly_ms, 1),
                "duplicate_ms": round(duplicate_ms, 1),
            }
            runs.append(run)
            self.stdout.write(
                f"seed {seed:>3} | anomalies P={run['anomalies']['precision']:.3f} "
                f"R={run['anomalies']['recall']:.3f} | doublons P={run['duplicates']['precision']:.3f} "
                f"R={run['duplicates']['recall']:.3f} | {anomaly_ms + duplicate_ms:.1f} ms"
            )

        summary = {
            "protocol": "500 dépenses générées, 30 anomalies (×5 à ×10) et 20 doublons injectés",
            "seeds": seeds,
            "anomalies": _mean_metrics(runs, "anomalies"),
            "duplicates": _mean_metrics(runs, "duplicates"),
            "detection_ms_mean": round(statistics.mean(r["anomaly_ms"] + r["duplicate_ms"] for r in runs), 1),
            "summary_latency": _summary_latency(),
            "targets": {"anomaly_recall": 0.80, "duplicate_precision": 0.85, "summary_seconds": 8},
            "runs": runs,
        }
        Path(output).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        anomalies, duplicates = summary["anomalies"], summary["duplicates"]
        self.stdout.write("")
        self.stdout.write(
            f"Moyenne anomalies : précision {anomalies['precision']:.3f}, rappel {anomalies['recall']:.3f} "
            f"(cible rappel >= 0,80) {'OK' if anomalies['recall'] >= 0.80 else 'NON ATTEINTE'}"
        )
        self.stdout.write(
            f"Moyenne doublons  : précision {duplicates['precision']:.3f}, rappel {duplicates['recall']:.3f} "
            f"(cible précision >= 0,85) {'OK' if duplicates['precision'] >= 0.85 else 'NON ATTEINTE'}"
        )
        self.stdout.write(f"Détection : {summary['detection_ms_mean']} ms en moyenne pour 500 dépenses")
        latency = summary["summary_latency"]
        if latency["calls"]:
            self.stdout.write(
                f"Résumé narratif (AIRequestLog) : {latency['calls']} appels, moyenne {latency['mean_s']} s, "
                f"p95 {latency['p95_s']} s, {latency['success_rate']:.0%} de succès"
            )
        else:
            self.stdout.write("Résumé narratif : aucun appel LLM journalisé pour l'instant.")
        self.stdout.write(self.style.SUCCESS(f"Métriques écrites dans {output}"))


def _mean_metrics(runs, key):
    return {
        metric: round(statistics.mean(run[key][metric] for run in runs), 3)
        for metric in ("precision", "recall", "f1")
    }


def _summary_latency():
    logs = list(
        AIRequestLog.objects.filter(module="analytics", feature="monthly_summary", latency_ms__isnull=False)
        .values_list("latency_ms", "success")
    )
    if not logs:
        return {"calls": 0}
    latencies = sorted(ms for ms, _ in logs)
    p95 = latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))]
    return {
        "calls": len(logs),
        "mean_s": round(statistics.mean(latencies) / 1000, 2),
        "p95_s": round(p95 / 1000, 2),
        "success_rate": round(sum(1 for _, ok in logs if ok) / len(logs), 3),
    }
