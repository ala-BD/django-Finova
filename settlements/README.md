# M3 · Soldes et remboursements (Imem)

Qui doit combien à qui, le minimum de virements, des remboursements confirmés par le
bénéficiaire, des échéanciers avec paiements partiels, et deux IA : le risque de retard
(régression logistique) et la relance rédigée par le LLM.

## Démarrer

```bash
python manage.py migrate
python manage.py m3_demo          # groupe de démo (DEBUG uniquement), --reset pour le recréer
python manage.py runserver
```

Comptes de démo : `demo_ala`, `demo_yahya`, `demo_linda`, mot de passe `finova-demo-2026`.
Depuis la fiche d'un groupe : boutons **Soldes** et **Remboursements**.

## Entités (CRUD Settlement ↔ RepaymentPlan)

| Modèle | Rôle |
|---|---|
| `Settlement` | Remboursement payeur → bénéficiaire (deux `Membership`), statut `pending` → `confirmed` / `rejected` |
| `RepaymentPlan` | Échéancier (1–1 avec Settlement) : n ≥ 2 échéances, fréquence, première date |
| `Installment` | Échéance : montant dû, montant payé (paiement partiel), date de paiement |
| `RiskPrediction` | Probabilité de retard, niveau, version du modèle, variables, latence |

## Routes (toutes sous `/groups/<gid>/`, réservées aux membres du groupe)

| URL | Règle |
|---|---|
| `balances/` | Solde net par membre + virements simplifiés (au plus n − 1) |
| `settlements/` | Liste filtrable par statut |
| `settlements/new/` | Le payeur est l'utilisateur connecté ; bénéficiaire ≠ payeur, membre actif ; montant > 0 |
| `settlements/<id>/` | Détail, échéancier, risque, relance |
| `settlements/<id>/confirm/`, `reject/` | POST, **bénéficiaire seul** (403 sinon), irréversible ; confirmation après paiement complet de l'échéancier |
| `settlements/<id>/plan/new/`, `edit/`, `delete/` | Payeur, bénéficiaire ou admin ; modification/suppression interdites après un premier paiement |
| `installments/<id>/pay/` | Payeur seul ; paiement partiel autorisé, jamais au-delà du reste dû |
| `settlements/<id>/risk/` | POST : recalcul de la prédiction |
| `settlements/<id>/reminder/` | POST (fetch, JSON) : relance générée, bénéficiaire seul |
| `settlements/<id>/reminder/send/` | POST : envoi au payeur via `notify` (M6) |

Un objet d'un autre groupe renvoie 404, un non-membre reçoit 403.

## Contrats avec les autres modules

- **Fourni** : `settlements.services.compute_balances(group) -> {user_id: Decimal}`. Solde net
  = payé − dû + remboursements confirmés envoyés − reçus. Tous les membres actifs sont présents.
  Utilisé par M1 (`can_leave`) et M6 (assistant).
- **Consommé, M2** : `expense_flows()` lit `Expense(group, payer→Membership, amount)` et
  `ExpenseShare(expense, member→Membership, amount_due)` (diagramme de classes §4.2). Tant que
  ces modèles n'existent pas, les soldes ne tiennent compte que des remboursements confirmés
  (un bandeau l'indique sur la page des soldes).
- **Consommé, M6** : `automation.services.notify(user, kind, message, link)` pour la déclaration,
  la confirmation, le rejet, la fin d'échéancier et la relance. Une notification qui échoue ne
  bloque jamais l'action.
- **Consommé, socle** : `core.ai.call_llm` (relance), `AIRequestLog` (latence des prédictions).

## Algorithme de simplification des dettes

À chaque étape, le plus gros débiteur rembourse le plus gros créancier du plus petit des deux
montants : au moins un solde tombe à zéro, donc au plus n − 1 virements.
Exemple du rapport : A +50, B −20, C −30 ⇒ C → A 30 et B → A 20. Le test de propriété vérifie
sur 200 cas aléatoires : ≤ n − 1 virements, montants positifs, tous les soldes soldés.

## IA (1) · Risque de retard

- Cible : une échéance payée avec **plus de 3 jours de retard**.
- Variables (`settlements/risk.py`) : montant, nombre d'échéances, retards passés du débiteur,
  délai moyen de confirmation, montant / poids d'équité (M1), jours depuis la création.
- **Jeu de démonstration généré** : `ml/m3_generate_dataset.py` (600 lignes, graine 42).
  Faute d'historique réel, la cible est simulée par une relation logistique bruitée, et c'est
  annoncé à la soutenance.
- Entraînement : `ml/m3_train_risk.py` (StandardScaler + LogisticRegression pondérée, 80/20 stratifié,
  seuil 0,5). Il produit `ml/m3_risk_model.joblib` (versionné) et `ml/m3_risk_metrics.json`.

| Jeu de test (120 lignes) | Modèle | Référence « jamais de retard » |
|---|---|---|
| Précision | 0,689 | 0 |
| Rappel | 0,738 | 0 |
| **F1** (cible ≥ 0,70) | **0,713** | 0 |
| Exactitude | 0,792 | 0,650 |
| Matrice de confusion | VN 64 · FP 14 · FN 11 · VP 31 | VN 78 · FN 42 |

Latence moyenne d'une prédiction : **0,5 ms** (cible < 200 ms). Chaque prédiction est aussi
journalisée dans `AIRequestLog` (`feature = m3.risk`).

```bash
python ml/m3_generate_dataset.py
python ml/m3_train_risk.py
```

## IA (2) · Relance générée

Le bénéficiaire choisit un ton (amical, neutre, ferme) ; `call_llm` rédige le message à partir
du montant restant, du retard et des prénoms. La sortie est validée : non vide, 600 caractères
au plus, et elle doit reprendre le montant. Si l'API est indisponible ou la sortie invalide, un
modèle de texte statique prend le relais. L'appel se fait en `fetch` avec un indicateur de
chargement (`aria-busy`). Le message reste modifiable avant l'envoi.

## Tests

```bash
python manage.py test settlements core
coverage run --source=settlements manage.py test settlements && coverage report
```

Couverture actuelle : modèles, services (dont le test de propriété), vues et permissions
(403/404, bénéficiaire seul), IA (modèle réel chargé, LLM simulé : aucun appel réseau).

## Rôle transversal : pages d'erreur et validation

- `templates/400.html`, `403.html`, `403_csrf.html`, `404.html` (gabarit commun `_error.html`),
  `500.html` autonome (sans base de données ni utilisateur, pour s'afficher même en cas de panne).
- Vues dans `core/views.py`, branchées par `handler400…500` et `CSRF_FAILURE_VIEW`.
- `templates/includes/form_errors.html` : résumé accessible des erreurs (`role="alert"`, liens vers
  les champs), réutilisable par tous les modules :
  `{% include "includes/form_errors.html" with form=form %}`.
- `templates/includes/messages.html` : messages flash avec préfixe lisible par les lecteurs
  d'écran (« Erreur : », « Succès : ») et `role="alert"` pour les erreurs.
- Tests dans `core/tests.py` avec `DEBUG=False` : 404, 403 (non-membre), 500 (vue qui lève une
  exception), échec CSRF.
