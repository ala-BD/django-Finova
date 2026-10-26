Jeux de données de démonstration, scripts d'entraînement et métriques IA.

Chaque module pose ici ses fichiers (`m2_categories.csv`, `m3_risk.csv`, etc.).
Les modèles sérialisés (joblib) sont versionnés avec le code.

## M4 · Prévision de fin de mois (Linda)

```bash
python ml/m4_generate_demo.py       # jeu généré → ml/data/m4_demo_expenses.csv
python ml/m4_evaluate_forecast.py   # validation temporelle → ml/results/m4_forecast_report.md
```

Résultat : MAE 45,58 TND contre 65,33 pour la prévision naïve, soit un gain de 30,2 % (cible ≥ 10 %).
