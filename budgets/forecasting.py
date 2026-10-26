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


# --- Risque de dépassement (modèle entraîné, voir ml/m4_train_overrun.py) ------------

OVERRUN_FEATURES = (
    "day_fraction",
    "spent_ratio",
    "forecast_ratio",
    "pace_vs_usual",
    "history_cv",
    "history_over_rate",
    "group_pace_vs_usual",
)


def _clip(value: float, low: float = 0.0, high: float = 3.0) -> float:
    return max(low, min(high, value))


def pace_ratio(spent, usual_spent) -> float:
    """Dépensé à ce jour / dépensé d'habitude à la même date (1 = rythme habituel)."""
    spent, usual_spent = float(spent), float(usual_spent)
    if usual_spent > 0:
        return _clip(spent / usual_spent)
    return 1.0 if spent == 0 else 2.0


def overrun_features(
    spent,
    forecast,
    limit,
    day: int,
    days_in_month: int,
    history_totals=(),
    history_cumulative_at_day=(),
    group_spent=0,
    group_usual_spent=0,
) -> dict:
    """
    Variables du modèle de risque, calculées de la même façon à l'entraînement
    et en production (sinon le modèle verrait des données différentes).

    - day_fraction      : avancement du mois (0 à 1)
    - spent_ratio       : part du budget déjà consommée
    - forecast_ratio    : prévision de fin de mois / limite
    - pace_vs_usual     : dépensé à ce jour / dépensé d'habitude à la même date
    - history_cv        : irrégularité des mois passés (écart-type / moyenne)
    - history_over_rate : part des mois passés qui auraient dépassé cette limite
    - group_pace_vs_usual : même rapport que pace_vs_usual, toutes catégories du
      groupe confondues (un mois chargé l'est souvent partout : invités, fêtes…)
    """
    from statistics import pstdev

    limit = float(limit)
    spent = float(spent)
    history_totals = [float(total) for total in history_totals]
    usual_spent = mean(float(c) for c in history_cumulative_at_day) if history_cumulative_at_day else 0.0
    average = mean(history_totals) if history_totals else 0.0
    return {
        "day_fraction": day / days_in_month,
        "spent_ratio": _clip(spent / limit),
        "forecast_ratio": _clip(float(forecast) / limit),
        "pace_vs_usual": pace_ratio(spent, usual_spent),
        "history_cv": _clip(pstdev(history_totals) / average) if len(history_totals) > 1 and average else 0.0,
        "history_over_rate": mean(1.0 if total > limit else 0.0 for total in history_totals)
        if history_totals
        else 0.5,
        "group_pace_vs_usual": pace_ratio(group_spent, group_usual_spent),
    }


def day_bucket(day_fraction: float) -> str:
    if day_fraction < 1 / 3:
        return "debut"
    if day_fraction < 2 / 3:
        return "milieu"
    return "fin"


def forecast_interval(predicted, spent, day_fraction: float, quantiles: dict) -> tuple[Decimal, Decimal]:
    """
    Fourchette à 80 % : on applique à la prévision les quantiles 10 % et 90 %
    du rapport réel / prévu observés à l'entraînement, au même moment du mois.
    La borne basse ne descend jamais sous ce qui est déjà dépensé.
    """
    low_ratio, high_ratio = quantiles[day_bucket(day_fraction)]
    predicted = float(predicted)
    low = max(predicted * low_ratio, float(spent))
    high = max(predicted * high_ratio, low)
    return to_decimal(low), to_decimal(high)
