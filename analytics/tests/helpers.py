from datetime import date
from decimal import Decimal
from itertools import count

from analytics.datasource import ExpenseRecord, ExpenseSource

_ids = count(1000)


def record(**overrides) -> ExpenseRecord:
    values = {
        "id": next(_ids),
        "group_id": 1,
        "group_name": "Colocation",
        "title": "Courses",
        "amount": Decimal("50.000"),
        "currency": "TND",
        "date": date(2026, 10, 10),
        "category": "Alimentation",
        "payer": "Sami",
    }
    values.update(overrides)
    if not isinstance(values["amount"], Decimal):
        values["amount"] = Decimal(str(values["amount"]))
    return ExpenseRecord(**values)


def sample_records():
    """Petit jeu déterministe sur deux mois, avec une anomalie évidente."""
    records = [
        record(id=1, title="Courses Carrefour", amount="60", date=date(2026, 9, 3)),
        record(id=2, title="Restaurant", amount="45", date=date(2026, 9, 12), payer="Mariem"),
        record(id=3, title="Taxi", amount="12", date=date(2026, 9, 20), category="Transport"),
        record(id=4, title="Courses Carrefour", amount="70", date=date(2026, 10, 2)),
        record(id=5, title="Cinéma", amount="18", date=date(2026, 10, 5), category="Loisirs", payer="Mariem"),
        record(id=6, title="Week-end", amount="100", date=date(2026, 10, 8), category="Loisirs",
               group_id=2, group_name="Paris", currency="EUR", payer="Wassim"),
    ]
    for index, amount in enumerate(["50", "55", "48", "62", "58", "52", "47", "60"], start=10):
        records.append(record(id=index, title="Marché", amount=amount, date=date(2026, 10, index)))
    records.append(record(id=30, title="Marché", amount="900", date=date(2026, 10, 25)))
    return records


def fake_source(records=None, is_demo=False):
    def loader(user):
        return ExpenseSource(records=list(sample_records() if records is None else records), is_demo=is_demo)

    return loader
