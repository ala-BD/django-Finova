# M4 · Risque de dépassement de budget — résultats

Modèle retenu : **gradient boosting** (F1 en validation croisée par groupe : logistic regression 0.812, gradient boosting 0.845).

Jeu : 60 colocations simulées — 26730 lignes d'entraînement (45 groupes), 8910 lignes de test (15 groupes jamais vus). Taux de dépassement réel dans le test : 39%.

> Données générées (aucune donnée réelle disponible) : annoncé à la soutenance.

## Test (seuil 0.40, choisi par validation croisée)

| Méthode | Précision | Rappel | F1 | Exactitude |
|---|---:|---:|---:|---:|
| **Modèle IA** | 0.807 | 0.859 | 0.832 | 0.866 |
| Règle « déjà 80 % consommés » | 0.511 | 0.490 | 0.500 | 0.622 |
| Règle « prévision > limite » | 0.820 | 0.772 | 0.795 | 0.847 |

ROC-AUC : **0.947** pour le modèle, 0.874 pour la prévision seule (score = prévision / limite).

## F1 selon le moment du mois

| Période | Modèle IA | Règle « prévision > limite » |
|---|---:|---:|
| debut | 0.752 | 0.689 |
| milieu | 0.851 | 0.812 |
| fin | 0.898 | 0.881 |

Le gain est le plus fort en début de mois, quand l'alerte est la plus utile.

## Matrice de confusion du modèle

| | Prédit : pas de dépassement | Prédit : dépassement |
|---|---:|---:|
| **Réel : pas de dépassement** | 4765 (vrais négatifs) | 707 (fausses alertes) |
| **Réel : dépassement** | 485 (dépassements manqués) | 2953 (vrais positifs) |

## Importance des variables

| Variable | Importance |
|---|---:|
| `forecast_ratio` | 0.710 |
| `group_pace_vs_usual` | 0.109 |
| `spent_ratio` | 0.100 |
| `history_over_rate` | 0.037 |
| `history_cv` | 0.030 |
| `pace_vs_usual` | 0.010 |
| `day_fraction` | 0.003 |

## Fourchette de prévision (80 %)

Couverture sur le test : **81%** des totaux réels tombent dans la fourchette (cible 80 %).

## Temps de réponse

Prédiction : 0.25 ms par budget (cible < 200 ms).

## Limites (à dire à l'oral)

- Données générées : dans le simulateur, un mois « chargé » touche toutes les catégories, ce qui rend `group_pace_vs_usual` très utile. Hypothèse plausible (invités, fêtes), à confirmer sur données réelles.
- Le modèle ne remplace pas la prévision : il la complète par une probabilité, plus fine qu'un oui/non.
