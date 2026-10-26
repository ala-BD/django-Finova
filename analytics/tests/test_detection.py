from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from analytics.demo import evaluation_dataset
from analytics.detection import (
    detect_anomalies,
    detect_duplicates,
    precision_recall,
    robust_scores,
    run_detection,
    sync_flags,
    title_similarity,
)
from analytics.models import AnomalyFlag
from analytics.tests.helpers import record, sample_records


class AnomalyDetectionTests(SimpleTestCase):
    def test_robust_score_is_zero_at_median(self):
        scores = robust_scores([Decimal(v) for v in ["10", "20", "30"]])
        self.assertAlmostEqual(scores[1], 0.0)

    def test_constant_amounts_do_not_divide_by_zero(self):
        scores = robust_scores([Decimal("900")] * 6)
        self.assertEqual(scores, [0.0] * 6)

    def test_spike_is_flagged_and_normal_values_are_not(self):
        detections = detect_anomalies(sample_records())
        self.assertEqual([d.record.id for d in detections], [30])
        self.assertEqual(detections[0].kind, AnomalyFlag.Kind.ANOMALY)
        self.assertIn("fois le montant médian", detections[0].reason)

    def test_small_categories_are_skipped(self):
        records = [record(amount=a, category="Santé") for a in ["30", "32", "500"]]
        self.assertEqual(detect_anomalies(records), [])

    def test_label_check_removes_false_positive_from_mixed_category(self):
        # Le carburant est cher pour « Transport », mais normal pour son libellé.
        records = [record(title="Ticket métro", amount=a, category="Transport") for a in ["7", "8", "8", "9", "8", "7"]]
        records += [record(title="Carburant", amount=a, category="Transport") for a in ["58", "60", "62", "61", "59"]]
        self.assertEqual(detect_anomalies(records), [])

    def test_categories_are_compared_within_their_group(self):
        group_a = [record(group_id=1, amount=a) for a in ["50", "52", "48", "51", "49", "50", "53", "47"]]
        group_b = [record(group_id=2, amount=a) for a in ["500", "520", "480", "510", "490", "505", "515", "495"]]
        self.assertEqual(detect_anomalies(group_a + group_b), [])


class DuplicateDetectionTests(SimpleTestCase):
    def setUp(self):
        self.original = record(id=1, title="Courses Carrefour", amount="63.400", date=date(2026, 10, 3))

    def test_same_payer_amount_close_date_and_title_is_duplicate(self):
        copy = record(id=2, title="courses carrefour", amount="63.400", date=date(2026, 10, 4))
        detections = detect_duplicates([self.original, copy])
        self.assertEqual([d.record.id for d in detections], [2])
        self.assertEqual(detections[0].kind, AnomalyFlag.Kind.DUPLICATE)

    def test_two_days_apart_is_not_duplicate(self):
        copy = record(id=2, title="Courses Carrefour", amount="63.400", date=date(2026, 10, 5))
        self.assertEqual(detect_duplicates([self.original, copy]), [])

    def test_other_payer_is_not_duplicate(self):
        copy = record(id=2, title="Courses Carrefour", amount="63.400", date=date(2026, 10, 3), payer="Mariem")
        self.assertEqual(detect_duplicates([self.original, copy]), [])

    def test_different_amount_is_not_duplicate(self):
        copy = record(id=2, title="Courses Carrefour", amount="63.500", date=date(2026, 10, 3))
        self.assertEqual(detect_duplicates([self.original, copy]), [])

    def test_dissimilar_title_is_not_duplicate(self):
        copy = record(id=2, title="Pharmacie", amount="63.400", date=date(2026, 10, 3))
        self.assertEqual(detect_duplicates([self.original, copy]), [])

    def test_similarity_ignores_case_and_spaces(self):
        self.assertEqual(title_similarity("Courses  Carrefour", "courses carrefour"), 1.0)

    def test_monthly_rent_is_not_a_duplicate(self):
        rents = [
            record(title="Loyer", amount="900", date=date(2026, 6, 1) + timedelta(days=30 * i)) for i in range(5)
        ]
        self.assertEqual(detect_duplicates(rents), [])


class EvaluationTests(SimpleTestCase):
    def test_precision_recall(self):
        metrics = precision_recall({1, 2, 3, 4}, {2, 3, 5})
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.667)
        self.assertEqual(metrics["false_negatives"], 1)

    def test_empty_sets(self):
        self.assertEqual(precision_recall(set(), set())["f1"], 0.0)

    def test_report_targets_are_met_on_generated_dataset(self):
        """Protocole du rapport : 500 dépenses, 30 anomalies et 20 doublons injectés."""
        dataset = evaluation_dataset(today=date(2026, 10, 26), seed=1)
        self.assertEqual(len(dataset.records), 500)
        self.assertEqual(len(dataset.anomaly_ids), 30)
        self.assertEqual(len(dataset.duplicate_ids), 20)
        anomalies = precision_recall({d.record.id for d in detect_anomalies(dataset.records)}, dataset.anomaly_ids)
        duplicates = precision_recall(
            {d.record.id for d in detect_duplicates(dataset.records)}, dataset.duplicate_ids
        )
        self.assertGreaterEqual(anomalies["recall"], 0.80)
        self.assertGreaterEqual(duplicates["precision"], 0.85)

    def test_detection_is_fast(self):
        dataset = evaluation_dataset(today=date(2026, 10, 26), seed=2)
        _, elapsed_ms = run_detection(dataset.records)
        self.assertLess(elapsed_ms, 500)


class SyncFlagsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zahra", password="un-mot-de-passe-fort")

    def test_sync_is_idempotent_and_keeps_review_state(self):
        detections, _ = run_detection(sample_records())
        self.assertEqual(sync_flags(self.user, detections), 1)
        AnomalyFlag.objects.update(reviewed=True)
        self.assertEqual(sync_flags(self.user, detections), 0)
        self.assertEqual(AnomalyFlag.objects.count(), 1)
        self.assertTrue(AnomalyFlag.objects.get().reviewed)
