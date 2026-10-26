import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from core.models import AIRequestLog


class EvaluateCommandTests(TestCase):
    def test_writes_metrics_and_reports_llm_latency(self):
        AIRequestLog.objects.create(module="analytics", feature="monthly_summary", latency_ms=1200, success=True)
        AIRequestLog.objects.create(module="analytics", feature="monthly_summary", latency_ms=3400, success=False)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "m5.json"
            out = StringIO()
            call_command("analytics_evaluate", "--seeds", "1", "--output", str(output), stdout=out)
            metrics = json.loads(output.read_text(encoding="utf-8"))
        self.assertGreaterEqual(metrics["anomalies"]["recall"], 0.80)
        self.assertGreaterEqual(metrics["duplicates"]["precision"], 0.85)
        self.assertEqual(metrics["summary_latency"]["calls"], 2)
        self.assertEqual(metrics["summary_latency"]["success_rate"], 0.5)
        self.assertIn("OK", out.getvalue())
