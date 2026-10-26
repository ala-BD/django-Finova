import io
import json
import urllib.error
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from analytics.currency import convert, convert_records, get_rate
from analytics.models import ExchangeRate
from analytics.tests.helpers import record

API_URL = "https://taux.example/{base}"


def api_response(rates):
    return io.BytesIO(json.dumps({"result": "success", "rates": rates}).encode())


@override_settings(EXCHANGE_RATE_API_URL="")
class CurrencyTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_same_currency(self):
        self.assertEqual(get_rate("TND", "TND").source, "identity")
        self.assertEqual(convert("12.5", "TND", "TND"), Decimal("12.500"))

    def test_fixed_rates_when_nothing_else_is_available(self):
        quote = get_rate("EUR", "TND")
        self.assertEqual((quote.rate, quote.source), (Decimal("3.400000"), "fixed"))
        self.assertEqual(convert("10", "EUR", "TND"), Decimal("34.000"))

    def test_last_known_rate_beats_fixed_rate(self):
        ExchangeRate.objects.create(base="EUR", target="TND", rate=Decimal("3.350000"))
        quote = get_rate("EUR", "TND")
        self.assertEqual((quote.rate, quote.source), (Decimal("3.350000"), "db"))
        self.assertEqual(get_rate("EUR", "TND").source, "cache")

    def test_cached_rate_is_used_first(self):
        cache.set("analytics:fx:EUR:TND", "3.2", 60)
        self.assertEqual(get_rate("EUR", "TND"), get_rate("EUR", "TND"))
        self.assertEqual(get_rate("EUR", "TND").source, "cache")

    def test_unsupported_currency(self):
        with self.assertRaises(ValueError):
            get_rate("JPY", "TND")

    @override_settings(EXCHANGE_RATE_API_URL=API_URL)
    def test_api_rate_is_stored_and_cached(self):
        with mock.patch("urllib.request.urlopen", return_value=api_response({"TND": 3.38, "USD": 1.08})) as urlopen:
            quote = get_rate("EUR", "TND")
            self.assertEqual((quote.rate, quote.source), (Decimal("3.380000"), "api"))
            self.assertEqual(get_rate("EUR", "USD").source, "cache")
        urlopen.assert_called_once()
        self.assertEqual(urlopen.call_args.args[0], "https://taux.example/EUR")
        self.assertTrue(ExchangeRate.objects.filter(base="EUR", target="USD").exists())

    @override_settings(EXCHANGE_RATE_API_URL=API_URL)
    def test_api_failure_falls_back(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("hors ligne")):
            self.assertEqual(get_rate("EUR", "TND").source, "fixed")

    def test_convert_records(self):
        records = [record(amount="10", currency="EUR"), record(amount="5", currency="TND")]
        converted, rates = convert_records(records, "TND")
        self.assertEqual([r.amount for r in converted], [Decimal("34.000"), Decimal("5.000")])
        self.assertEqual({r.currency for r in converted}, {"TND"})
        self.assertEqual(list(rates), ["EUR"])
