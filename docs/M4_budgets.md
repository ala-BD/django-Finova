# M4 · Budgets et simulateur

**Responsable :** Linda · **Branche :** `feature/m4-budgets` · **App Django :** `budgets`

Rôle transversal de Linda : intégration et gel du modèle de données commun (section 1),
modèle `User` et données de démonstration (section 2).

---

## 1. Gel du modèle de données commun

Tous les modèles des diagrammes de classes du rapport existent maintenant, avec leurs
migrations initiales (`python manage.py migrate` passe sans erreur). Chaque responsable reste
propriétaire de son module : **services, vues, formulaires et tests** sont à écrire par lui.
Toute modification d'un modèle passe désormais par une demande à l'équipe et une pull request.

| App | Modèles | Choix structurants |
|---|---|---|
| `accounts` | `User` | `AbstractUser` + avatar, devise, profil de revenu, `monthly_income` ; `get_groups()` |
| `groups` (M1) | `Group`, `Membership`, `Invitation` | un seul membership par (user, groupe) ; poids > 0 ; jeton UUID, expiration 7 jours |
| `expenses` (M2) | `Expense`, `ExpenseItem`, `ItemClaim`, `ExpenseShare` | `Expense.category` en **PROTECT** ; montant > 0 ; quantité ≥ 1 |
| `settlements` (M3) | `Settlement`, `RepaymentPlan`, `Installment`, `RiskPrediction` | payeur ≠ bénéficiaire ; `paid_amount ≤ amount` |
| `budgets` (M4) | `Category`, `Budget`, `BudgetForecast` | voir section 3 |
| `analytics` (M5) | `SavedFilter`, `Report`, `AnomalyFlag`, `ExchangeRate` | nom de filtre unique par utilisateur |
| `automation` (M6) | `RecurringExpense`, `ReminderRule`, `Notification`, `ChatMessage` | `days_before ≤ 30` ; index (user, is_read) |
| `core` | `AIRequestLog` | journal de latence de toutes les IA |

Conventions appliquées partout : montants en `DecimalField(max_digits=12, decimal_places=3)`
(millime TND, jamais de `float`), contraintes métier aussi en base (`CheckConstraint`,
`UniqueConstraint`), `related_name` explicites, libellés en français.

Le contrat `expenses.services.get_group_expenses(group, period, category)` a été écrit
dans sa version minimale (filtre sur groupe, période `(début, fin)` et catégorie, retourne un
QuerySet) pour que M3, M4, M5 et M6 puissent avancer ; Yahya garde la main sur ce fichier.

## 2. Données de démonstration

```bash
python manage.py seed_demo            # crée les données si elles sont absentes
python manage.py seed_demo --reset    # supprime puis recrée
```

Crée les 6 membres de l'équipe (mot de passe `Finova-demo-2026`), le groupe
« Coloc Lac 2 (démo) » avec poids et rôles (Linda et Ala admins), 6 catégories, 7 mois de
dépenses générées (graine fixe, donc reproductible) et un budget par catégorie et par mois.
Se connecter en `linda` (admin) ou `yahya` (membre simple) pour montrer les deux vues.

## 3. Module M4

### Entités

- **Category** (`group`, `name`, `color`, `icon`) : nom unique par groupe, sans tenir compte de la casse.
- **Budget** (`group`, `category`, `month`, `limit_amount`, `carry_over`, `alert_80_sent`, `alert_100_sent`) :
  un seul budget par catégorie et par mois ; limite > 0 ; `month` = premier jour du mois.
- **BudgetForecast** (`budget`, `predicted_total`, `baseline_total`, `method`) : une prévision gardée par budget et par jour.

### URLs et droits

| Action | URL | Membre | Admin |
|---|---|:-:|:-:|
| Vue d'ensemble | `/budgets/` | ✓ | ✓ |
| Budgets du mois | `/groups/<gid>/budgets/?month=2026-10` | ✓ | ✓ |
| Créer / modifier / supprimer un budget | `…/budgets/new/`, `…/<id>/edit/`, `…/<id>/delete/` | 403 | ✓ |
| Catégories (liste) | `/groups/<gid>/categories/` | ✓ | ✓ |
| Créer / modifier / supprimer une catégorie | `…/categories/new/`, `…/<id>/edit/`, `…/<id>/delete/` | 403 | ✓ |
| Simulateur (HTML ou JSON) | `/groups/<gid>/budgets/simulate/` | ✓ | ✓ |
| Recommandation IA (JSON) | `/groups/<gid>/budgets/recommend/?category=<id>` | ✓ | ✓ |

Visiteur → redirection vers la connexion avec `?next=`. Non-membre → **403** sur toutes ces URLs.
Budget d'un autre groupe → **404**. Suppressions en POST avec jeton CSRF uniquement.

### Règles de gestion

- **% consommé** = Σ dépenses de la catégorie sur le mois / limite effective, agrégé par la base
  (`aggregate(Sum("amount"))`), jamais en Python.
- **Alertes 80 % et 100 %** : une seule notification par seuil et par mois, envoyée à tous les
  membres actifs via `automation.services.notify`. Le drapeau est levé par un UPDATE conditionnel
  (`… WHERE alert_80_sent = false`) : deux dépenses simultanées ne déclenchent qu'un envoi.
  Si `notify` échoue, le drapeau est rabaissé et l'alerte repartira plus tard. Déclenchement :
  signal `post_save` sur `Expense` (M2 n'a rien à appeler), après chaque création ou
  modification de budget, et commande `check_budget_alerts` pour le planificateur.
- **Report** : si `carry_over` est coché sur le budget de septembre, le reste non dépensé
  (jamais négatif) s'ajoute à la limite d'octobre ; les reports s'enchaînent sur 12 mois maximum.
- **Suppression d'une catégorie** refusée tant que des dépenses y sont rattachées, sauf
  réaffectation à une autre catégorie (et la base l'interdit aussi grâce à `PROTECT`).
- **Simulateur « et si ? »** : économie mensuelle = moyenne mensuelle de la catégorie × % de
  réduction ; délai = objectif / économie, arrondi au mois supérieur. Exemple du rapport :
  300 TND/mois, −20 % → 60 TND/mois, objectif 360 TND → **6 mois** (test automatisé).
  Lecture seule, donc en GET ; avec JavaScript, appel `fetch` et réponse JSON sans rechargement ;
  sans JavaScript, le même formulaire affiche le résultat côté serveur.

### Contrat exposé

```python
from budgets.services import get_budget_status
get_budget_status(group, category, month)
# {"budget", "limit", "carried_over", "effective_limit", "consumed",
#  "remaining", "percent", "level": "ok" | "warning" | "over" | "none", ...}
```

## 4. Partie IA

### Prévision de fin de mois (`budgets/forecasting.py`)

`total prévu = déjà dépensé au jour d + reste estimé`, avec une méthode choisie selon la quantité de données :

| Historique | Méthode | Reste estimé |
|---|---|---|
| ≥ 2 mois | profil historique | moyenne de ce qui a été dépensé après le jour d les mois précédents |
| 1 mois | rythme + historique | moyenne du profil et de la régression |
| aucun | rythme actuel | pente d'une régression linéaire (scikit-learn) du cumul quotidien × jours restants |

La prévision est affichée sur chaque budget du mois en cours, avec « dépassement probable de X »
si elle dépasse la limite effective. Chaque calcul est journalisé dans `AIRequestLog`
(module `budgets`, feature `forecast`).

### Évaluation honnête (validation temporelle)

```bash
python ml/m4_generate_demo.py       # 14 mois générés → ml/data/m4_demo_expenses.csv
python ml/m4_evaluate_forecast.py   # → ml/results/m4_forecast_report.md (+ .json)
```

Pour chaque mois (à partir du 3e), chaque catégorie et chaque jour de contrôle (5, 10, 15, 20, 25),
le modèle ne voit que le passé. 360 prévisions évaluées :

| Méthode | MAE (TND) | RMSE (TND) | Gain vs naïve |
|---|---:|---:|---:|
| Naïve (moyenne historique) | 65,33 | 108,03 | 0 % |
| Régression linéaire seule | 104,83 | 159,79 | −60,4 % |
| Mélange régression + profil | 66,44 | 101,48 | −1,7 % |
| **Finova M4** | **45,58** | **78,86** | **+30,2 %** |

Cible du rapport (gain ≥ 10 %) atteinte. Latence du calcul : 0,07 ms en moyenne (cible < 1 s).
**Pourquoi pas la régression seule ?** Elle extrapole le rythme : un loyer de 1 200 TND payé
le 1er donnerait une prévision de 36 000 TND. Elle reste utile comme repli quand une catégorie
n'a encore aucun historique. Données générées : à annoncer clairement à la soutenance.

### Recommandation de budget

Limite conseillée = moyenne des 3 derniers mois + 10 %, arrondie à la dizaine supérieure
(280, 300, 320 → 330). L'explication en une phrase est demandée au LLM via `core.ai.call_llm`
(clé `LLM_API_KEY`). La réponse n'est acceptée que si elle contient bien le montant conseillé
et fait moins de 400 caractères ; sinon, ou si l'API est indisponible, un texte statique est
affiché. Bouton « Suggérer une limite » dans le formulaire de budget, avec indicateur de
chargement (`aria-busy`, texte « Analyse en cours… ») et région `aria-live`.

## 5. Tests et couverture

```bash
python manage.py test budgets accounts
coverage run --source=budgets,accounts manage.py test budgets accounts
coverage report --omit="*/migrations/*,*/tests/*,*/tests.py"
```

87 tests, **97 % de couverture** sur `budgets` + `accounts`. Aucun appel réseau : `notify` et
`call_llm` sont simulés (mock).

- `test_models` : unicité budget/catégorie/mois, limite > 0, nom unique insensible à la casse, PROTECT.
- `test_services` : % consommé sur un jeu connu, alertes uniques 80 % / 100 %, signal, report
  (simple, désactivé, négatif, en chaîne), simulateur (cas 300 TND / 20 %), prévision, recommandation.
- `test_forecasting` : séries synthétiques à tendance connue, dépense ponctuelle, comparaison à la naïve.
- `test_views` : visiteur → login ; non-membre → 403 sur toutes les URLs ; membre en lecture seule ;
  404 inter-groupes ; CSRF ; CRUD complet ; JSON du simulateur (statut et structure).
- `test_commands` : `check_budget_alerts` idempotente, `seed_demo` reproductible.

## 6. Accessibilité

Barres de progression `role="progressbar"` avec `aria-valuenow` et `aria-valuetext`, plus le
pourcentage et un libellé écrits (« Attention : 80 % atteints ») : l'information n'est jamais
portée par la seule couleur, et un motif hachuré marque le dépassement. Tableaux avec `caption`
et `scope`, liens d'action explicites pour les lecteurs d'écran (« Modifier le budget
Restaurants »), sous-navigation avec `aria-current`, résultats dynamiques en `aria-live`,
animation désactivée si `prefers-reduced-motion`, mise en page fluide jusqu'au mobile.

## 7. Scénario de démo live (3 minutes)

1. `seed_demo --reset`, connexion `linda`. Menu **Budgets** → groupe → mois en cours.
2. Montrer une carte « Attention : 80 % atteints » et une prévision « dépassement probable ».
3. **Nouveau budget** → choisir une catégorie → **Suggérer une limite** (repli visible si pas de clé) → enregistrer.
4. En attendant le formulaire de dépense de M2 : avec un superutilisateur (`createsuperuser`),
   ajouter dans l'admin (`/admin/expenses/expense/add/`) une dépense qui fait passer un budget
   au-dessus de 100 % → rafraîchir la page des budgets.
5. **Simulateur** : Restaurants, 20 %, 360 TND → résultat sans rechargement.
6. Se reconnecter en `yahya` : boutons de gestion absents, `/budgets/new/` → 403.

## 8. Questions probables à l'oral

- **Comment est calculé le % consommé ?** Somme ORM (`Sum`) des dépenses de la catégorie entre
  le 1er et le dernier jour du mois, divisée par la limite effective (limite + report).
- **Comment éviter deux fois la même alerte ?** Drapeaux `alert_80_sent` / `alert_100_sent`
  levés par un UPDATE conditionnel atomique ; rabaissés si l'envoi échoue, ou si la limite est
  relevée et qu'on repasse sous le seuil.
- **MAE / RMSE ?** Erreur absolue moyenne et racine de l'erreur quadratique moyenne, en TND.
  La RMSE pénalise davantage les grosses erreurs.
- **Pourquoi comparer à une prévision naïve ?** Un modèle n'a de valeur que s'il fait mieux
  qu'une règle simple ; ici +30 % de précision en MAE.
- **Pourquoi des données générées ?** Pas d'historique réel pour une nouvelle application ;
  le script est versionné, la graine est fixe et c'est annoncé.
