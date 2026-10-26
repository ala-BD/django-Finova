# Finova V2

Application Django de gestion et de partage de dépenses.

Ce dépôt est le **squelette commun**. Chaque membre implémente son module sur sa propre branche.

## Démarrage local

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows : ou cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Ouvrir http://127.0.0.1:8000/

## Applications

| App | Module | Responsable |
|---|---|---|
| `accounts` | Authentification partagée | toute l'équipe |
| `groups` | M1 Groupes et équité | Ala |
| `expenses` | M2 Dépenses et tickets | Yahya |
| `settlements` | M3 Soldes et remboursements | Imem |
| `budgets` | M4 Budgets et simulateur | Linda |
| `analytics` | M5 Analytics et rapports | Zahra |
| `automation` | M6 Automatisation et assistant | Wassim |
| `core` | Client LLM, permissions, utilitaires | transversal |

Les **modèles métier** (sauf `accounts.User` et `core.AIRequestLog`) sont volontairement vides : Linda anime le gel du modèle en S1. Jusque-là, personne ne modifie un modèle d'un autre module.

## Contrats entre modules (`services.py`)

- `groups.services.is_member` / `is_admin`
- `expenses.services.get_group_expenses`
- `settlements.services.compute_balances`
- `budgets.services.get_budget_status`
- `automation.services.notify`
- `core.ai.call_llm`

## Branches Git

```
main
└── develop
    ├── feature/m1-groupes
    ├── feature/m2-depenses
    ├── feature/m3-soldes
    ├── feature/m4-budgets
    ├── feature/m5-analytics
    └── feature/m6-automatisation
```

Commits : `type(portée): message`  
Exemple : `feat(m1): invitation par jeton expirant`

## Variables d'environnement

Voir `.env.example`. Ne jamais committer `.env`.

## M5 · Analytics et rapports (Zahra)

URL : `/analytics/` (tableau de bord), `/analytics/filters/`, `/analytics/reports/`, `/analytics/anomalies/`.

- **Données** : `analytics/datasource.py` lit les dépenses via `expenses.services.get_group_expenses`.
  Tant que M1/M2 ne sont pas livrés, un jeu de démonstration généré est utilisé et signalé à l'écran
  (`ANALYTICS_DEMO_DATA=false` pour le désactiver).
- **IA** : anomalies (score robuste MAD par catégorie, confirmé par libellé), doublons (difflib ≥ 0,8),
  résumé narratif du mois par LLM à partir des seuls agrégats, chiffres vérifiés, repli sur un modèle de texte.
- **Taux de change** : cache 24 h → API (`EXCHANGE_RATE_API_URL`) → derniers taux connus → taux fixes.
- **Métriques** : `python manage.py analytics_evaluate` (écrit `ml/m5_metrics.json`).
- **Tests et couverture** : `python manage.py test analytics` ;
  `coverage run manage.py test && coverage report` (configuration dans `.coveragerc`).

## Automatisation M6

Les commandes planifiables sont disponibles sans Redis :

```bash
python manage.py generate_recurring
python manage.py send_recurring_reminders
```

En production, configurer deux tâches cron Render (ou un planificateur équivalent)
qui exécutent ces commandes quotidiennement avec le même `DATABASE_URL`,
`DJANGO_SETTINGS_MODULE` et les variables email du service web. Les fréquences
hebdomadaires, mensuelles et annuelles sont calculées à partir de `next_run`.
Les occurrences et rappels ont une contrainte d'unicité en base afin de limiter
les doublons lors de relances ou d'exécutions concurrentes.

Celery est présent dans le dépôt et les tâches sont aussi exposées dans
`automation.tasks`, mais le déploiement Render ne configure pas de Redis :
les commandes cron sont donc le mécanisme d'exécution recommandé. Pour les emails,
configurer `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` et `DEFAULT_FROM_EMAIL`. La clé IA est
fournie via `LLM_API_KEY`; elle n'est jamais stockée dans le dépôt.

L'intégration M6 attend les contrats des modules concernés :
`groups.Group` avec `memberships`, `expenses.services.create_expense`,
`expenses.services.get_group_expenses`, `settlements.services.compute_balances`
et `budgets.services.get_budget_status`. Tant que ces modèles et services ne
sont pas intégrés, les vues liées aux groupes répondent explicitement que M1 est
indisponible et la génération récurrente journalise l'indisponibilité de M2.
En attendant le modèle M1, M6 conserve le groupe sous forme de `group_id` indexé
sans créer de table de groupe concurrente; une migration vers une véritable clé
étrangère devra suivre l'intégration du modèle officiel.
