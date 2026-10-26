# M4 · Prévision de fin de mois — résultats

Jeu : `ml/data/m4_demo_expenses.csv` — 14 mois, 6 catégories, 360 prévisions évaluées (points de contrôle aux jours 5, 10, 15, 20, 25).

> Données générées (aucune donnée réelle disponible) : annoncé à la soutenance.

## Validation temporelle (tous points de contrôle)

| Méthode | MAE (TND) | RMSE (TND) | Gain MAE vs naïve |
|---|---:|---:|---:|
| Naïve (moyenne historique) | 65.33 | 108.03 | +0.0 % |
| Régression linéaire du mois en cours | 104.83 | 159.79 | -60.4 % |
| Mélange régression + profil | 66.44 | 101.48 | -1.7 % |
| **Finova M4 (profil historique)** | 45.58 | 78.86 | +30.2 % |

## MAE selon le jour de la prévision

| Jour | Naïve | Finova M4 |
|---:|---:|---:|
| 5 | 65.33 | 58.28 |
| 10 | 65.33 | 47.88 |
| 15 | 65.33 | 47.55 |
| 20 | 65.33 | 41.48 |
| 25 | 65.33 | 32.73 |

## MAE par catégorie

| Catégorie | Naïve | Finova M4 |
|---|---:|---:|
| Alimentation | 195.09 | 114.75 |
| Factures | 21.48 | 5.20 |
| Logement | 0.00 | 0.00 |
| Loisirs | 39.21 | 36.18 |
| Restaurants | 82.69 | 83.13 |
| Transport | 53.55 | 34.22 |

## Temps de réponse (calcul de la prévision, hors base de données)

Moyenne 0.07 ms · p95 0.14 ms · max 0.18 ms (cible < 1 s).

## Lecture

- Cible du rapport : MAE modèle < MAE naïve et gain ≥ 10 % → **+30.2 %** (atteinte).
- La naïve ignore ce qui a déjà été dépensé ce mois-ci : elle se trompe dès que le mois est inhabituel.
- La régression linéaire extrapole le rythme : elle surestime les catégories à dépense ponctuelle (loyer le 1er, factures le 8), d'où une erreur pire que la naïve. Elle ne sert donc que de repli quand il n'existe aucun historique.
- Le profil historique combine le réel déjà dépensé et le reste habituel : c'est la méthode retenue.
- Plus la prévision est faite tard dans le mois, plus elle est précise (voir le tableau par jour).
