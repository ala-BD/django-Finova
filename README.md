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
python manage.py seed_demo        # données de démonstration (mot de passe : Finova-demo-2026)
python manage.py createsuperuser  # facultatif, pour l'admin Django
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

Le **modèle de données est figé** (gel S1, intégration : Linda) : tous les modèles des diagrammes de classes existent avec leurs migrations initiales. Détail et conventions dans [docs/M4_budgets.md](docs/M4_budgets.md#1-gel-du-modèle-de-données-commun). Toute modification d'un modèle passe par une demande à l'équipe et une pull request.

## Contrats entre modules (`services.py`)

- `groups.services.is_member` / `is_admin`
- `expenses.services.get_group_expenses(group, period=(début, fin), category)` → QuerySet
- `settlements.services.compute_balances`
- `budgets.services.get_budget_status(group, category, month)` → dict (`percent`, `level`, `effective_limit`…)
- `automation.services.notify`
- `core.ai.call_llm`

## Documentation des modules

- [M4 · Budgets et simulateur](docs/M4_budgets.md) — Linda (règles, IA, métriques, démo, questions d'oral)

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
