from decimal import Decimal

from django.test import TestCase

from core.rounding import largest_remainder_rounding


class RoundingTests(TestCase):
    def test_sum_equals_total(self):
        parts = {"a": Decimal("15.001"), "b": Decimal("15.001"), "c": Decimal("15.001")}
        rounded = largest_remainder_rounding(parts, Decimal("45.003"), places=3)
        self.assertEqual(sum(rounded.values()), Decimal("45.003"))
