"""
Partie IA de M4 : prévision de fin de mois et recommandation de budget.

Fonctions pures (aucun accès à la base) : elles sont appelées par
budgets/services.py en production et par ml/m4_evaluate_forecast.py pour la
validation temporelle. Les montants entrent en float ou Decimal et sortent en
Decimal arrondi au millime.

Méthode de prévision (expliquable à l'oral), choisie selon la quantité de données :

    total prévu = déjà dépensé au jour d + reste estimé jusqu'à la fin du mois

- au moins 2 mois d'historique → « profil historique » : le reste estimé est
  la moyenne de ce qui a été dépensé entre le jour d et la fin du mois, sur les
  mois précédents. Il respecte les dépenses ponctuelles (loyer le 1er,
  factures le 8) qu'une extrapolation linéaire surestime ;
- aucun historique → « rythme actuel » : pente d'une régression linéaire
  (scikit-learn) du cumul quotidien du mois en cours, extrapolée ;
- 1 seul mois d'historique → moyenne des deux estimations.

Ce choix vient de la validation temporelle (ml/m4_evaluate_forecast.py) :
sur le jeu de démonstration, le profil historique bat la prévision naïve
d'environ 30 % en MAE, alors que l'extrapolation linéaire seule fait pire
que la naïve. La prévision naïve de référence est la moyenne historique des
totaux mensuels.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from statistics import mean

MILLIME = Decimal("0.001")
MIN_HISTORY_FOR_PROFILE = 2


def to_decimal(value) -> Decimal:
    return Decimal(str(value)).quantize(MILLIME, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Forecast:
    predicted_total: Decimal
    baseline_total: Decimal | None
    spent_so_far: Decimal
    method: str


def naive_forecast(history_totals) -> Decimal | None:
    """Prévision de référence : moyenne historique des totaux mensuels."""
    if not history_totals:
        return None
    return to_decimal(mean(float(total) for total in history_totals))


def daily_rate(daily_amounts) -> float:
    """Pente (montant par jour) de la régression linéaire du cumul quotidien."""
    days = len(daily_amounts)
    if days == 0:
        return 0.0
    cumulative = []
    running = 0.0
    for amount in daily_amounts:
        running += float(amount)
        cumulative.append(running)
    if days < 3:
        return max(cumulative[-1] / days, 0.0)

    from sklearn.linear_model import LinearRegression

    x = [[day] for day in range(1, days + 1)]
    model = LinearRegression().fit(x, cumulative)
    return max(float(model.coef_[0]), 0.0)


def forecast_month_total(
    daily_amounts,
    days_in_month: int,
    history_totals=(),
    history_cumulative_at_day=None,
) -> Forecast:
    """
    Prévoit le total de fin de mois d'une catégorie.

    - daily_amounts : dépenses du mois en cours, jour par jour (jours 1 à d)
    - days_in_month : nombre de jours du mois prévu
    - history_totals : totaux des mois précédents
    - history_cumulative_at_day : pour chacun de ces mois, le cumul au jour d
      (facultatif ; sinon on suppose une dépense régulière dans le mois)
    """
    elapsed = min(len(daily_amounts), days_in_month)
    daily_amounts = list(daily_amounts)[:elapsed]
    spent = sum(float(amount) for amount in daily_amounts)
    remaining_days = days_in_month - elapsed
    history_totals = [float(total) for total in history_totals]
    baseline = naive_forecast(history_totals)

    if remaining_days == 0:
        return Forecast(to_decimal(spent), baseline, to_decimal(spent), "mois terminé")

    if not history_totals:
        if elapsed == 0:
            return Forecast(to_decimal(0), None, to_decimal(0), "aucune donnée")
        pace_remaining = daily_rate(daily_amounts) * remaining_days
        return Forecast(
            to_decimal(spent + pace_remaining), None, to_decimal(spent), "rythme actuel"
        )

    usual_remaining = historical_remaining(
        history_totals, history_cumulative_at_day, remaining_days, days_in_month
    )
    if len(history_totals) >= MIN_HISTORY_FOR_PROFILE or elapsed == 0:
        return Forecast(
            to_decimal(spent + usual_remaining), baseline, to_decimal(spent), "profil historique"
        )
    pace_remaining = daily_rate(daily_amounts) * remaining_days
    blended = (usual_remaining + pace_remaining) / 2
    return Forecast(
        to_decimal(spent + blended), baseline, to_decimal(spent), "rythme + historique"
    )


def historical_remaining(
    history_totals, history_cumulative_at_day, remaining_days, days_in_month
) -> float:
    """Moyenne de ce qui restait à dépenser après le jour d, sur les mois précédents."""
    if history_cumulative_at_day and len(history_cumulative_at_day) == len(history_totals):
        return mean(
            max(total - float(cum), 0.0)
            for total, cum in zip(history_totals, history_cumulative_at_day)
        )
    # Sans le détail quotidien, on suppose une dépense régulière dans le mois.
    return mean(history_totals) * remaining_days / days_in_month


def recommend_limit(history_totals, margin=Decimal("0.10"), step=Decimal("10")) -> Decimal | None:
    """
    Limite conseillée : moyenne des trois derniers mois + marge, arrondie au
    multiple de `step` supérieur (un budget « rond » est plus facile à retenir).
    """
    recent = [Decimal(str(total)) for total in list(history_totals)[-3:]]
    if not recent or sum(recent) <= 0:
        return None
    average = sum(recent) / len(recent)
    raw = average * (1 + Decimal(margin))
    return (raw / step).to_integral_value(rounding=ROUND_CEILING) * step
