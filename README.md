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

En développement, le backend e-mail `console` affiche les messages dans le terminal
et ne les envoie pas aux destinataires. Pour envoyer les invitations par e-mail,
configurer `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`,
`EMAIL_PORT`, `EMAIL_HOST_USER` et `EMAIL_HOST_PASSWORD` dans `.env`. Utiliser les
identifiants SMTP ou le mot de passe d'application du fournisseur, jamais le mot de
passe habituel du compte.

Pour que les liens d'invitation soient accessibles aux destinataires, configurer
`PUBLIC_BASE_URL` avec l'URL publique de l'application, par exemple
`https://finova.example.com`. Sans cette valeur, Django construit le lien depuis
l'adresse utilisée par l'administrateur ; en local, ce sera généralement
`http://127.0.0.1:8000`, accessible uniquement depuis sa machine.
