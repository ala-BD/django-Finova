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

Comptes de démo : `demo_imem`, `demo_ala`, `demo_yahya`, `demo_linda`, `demo_zahra`, `demo_wassim`, mot de
passe `finova-demo-2026`.
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
| `settlements/<id>/simulate/` | Simulateur interactif du risque (calcul dans le navigateur) |
| `settlements/parse/` | POST (fetch, JSON) : phrase → champs du formulaire |
| `settlements/<id>/plan/suggest/` | POST (fetch, JSON) : échéanciers abordables les moins risqués |
| `settlements/<id>/plan/pdf/`, `plan/ics/` | Export de l'échéancier (membres du groupe) |
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

### Explication du score (« Pourquoi ce score ? »)

Sur chaque échéancier, chaque variable affiche sa contribution au score : coefficient du modèle
× valeur standardisée, c'est-à-dire l'écart par rapport à un remboursement moyen du jeu
d'entraînement. Le tableau est trié par importance, avec une barre et un libellé texte
(« augmente / diminue le risque »). C'est exact pour une régression logistique : le score
(log-odds) est la somme de ces contributions et de la constante. Voir `risk.explain`.

## IA (4) · Suggestion de l'échéancier le plus sûr

Sur le formulaire d'échéancier, on indique ce que le payeur peut verser par mois. L'IA évalue
les 33 échéanciers possibles (2 à 12 échéances × hebdomadaire / bimensuel / mensuel) avec le
modèle de risque, garde ceux dont la charge mensuelle tient dans ce budget et propose les
3 moins risqués (bouton « Utiliser » pour remplir le formulaire). Voir `risk.rank_plans`, une
fonction pure, et `services.suggest_plans`.

Mesure : `python ml/m3_evaluate_suggestion.py`, sur les 120 remboursements du jeu de test
avec une capacité mensuelle tirée au hasard (remboursement en 1,5 à 8 mois).

| Comparaison | Cas | Risque moyen sans → avec IA | Risque élevé (≥ 50 %) |
|---|---|---|---|
| vs « étaler au maximum » (12 mensualités) | 120 | 54 % → **40 %** (−14 pts) | 60 → **37** |
| vs défaut « 3 mensualités », quand abordable | 31 | 43 % → 42 % (−0,5 pt) | 12 → 12 |

La suggestion n'est jamais pire que ces deux choix. Latence : 0,9 ms en moyenne.
**Limites à dire à l'oral :** le gain est mesuré avec le modèle lui-même, sur un jeu généré.
La fréquence n'est pas une variable du modèle : elle ne joue que sur la charge mensuelle.
L'apport vient surtout du fait d'éviter les échéanciers trop longs.

## IA (3) · Déclarer un remboursement en une phrase

Sur « Déclarer un remboursement » : « J'ai remboursé 30 dinars à Ala hier en espèces » remplit
le bénéficiaire, le montant et la note. Le LLM renvoie un JSON validé par Django : l'id doit
être un membre du groupe autre que le payeur, et le montant doit être > 0 et ≤ 1 000 000. Les
champs manquants ou invalides sont complétés par une analyse locale par règles. Celle-ci gère
la devise, les décimales à virgule, les dates à ne pas confondre avec un montant, les prénoms
mal orthographiés (difflib) et le mode de paiement. Sans clé API, seule l'analyse locale tourne.
Le formulaire est seulement pré-rempli : l'utilisateur valide.

Mesure : `python manage.py m3_evaluate_nl` sur `ml/m3_nl_sentences.csv` (30 phrases étiquetées)
→ `ml/m3_nl_metrics.json`.

| Mode | Bénéficiaire | Montant | Mode de paiement | Phrase entièrement correcte | Latence |
|---|---|---|---|---|---|
| Analyse locale | 100 % | 100 % | 100 % | 100 % | < 1 ms |
| LLM (Gemini, + règles pour les montants avec devise) | 100 % | 100 % | 97 % | 97 % | 735 ms (p95 970 ms) |

Améliorations guidées par la mesure réelle, à raconter à l'oral :
- Le LLM écrivait le mode de paiement librement (« cash », « D17 ») : mode 47 %. Il choisit
  désormais dans la liste fermée de l'application, validée par Django : mode 97 %.
- Gemini a lu « 47.300 dinars » comme 47 300 au lieu de 47,3 (millimes tunisiens). Un montant
  écrit à côté d'une devise est donc lu par les règles : montant 100 %.
- L'erreur restante, « j'ai versé 300 dt », est classée « Virement » par le LLM. C'est discutable
  en français, et elle est conservée telle quelle.

**Limite à dire à l'oral :** les 30 phrases ont été écrites par le même développeur que les
règles. Ce score est donc optimiste. Pour une mesure honnête, demander à 2 ou 3 camarades
d'écrire 20 phrases chacun sans voir le code, puis relancer la commande.

## IA interactive (5) · Simulateur « et si ? » du risque

`settlements/<id>/simulate/`, accessible depuis la carte « Risque de retard » de chaque
remboursement, est ouvert à tous les membres. Des curseurs règlent le nombre d'échéances, la
fréquence, le montant, ainsi que le profil du payeur : retards passés, délai de confirmation,
ancienneté. À chaque mouvement, la jauge, le verdict, l'écart avec le risque enregistré,
l'effort mensuel, les barres « Pourquoi ce score ? » et la courbe « risque selon le nombre
d'échéances » se mettent à jour. On peut cliquer un point de la courbe pour le choisir.
« Appliquer cet échéancier » ouvre le formulaire pré-rempli ; il n'apparaît que pour le payeur,
le bénéficiaire ou un admin, et seulement avant tout paiement.

- **Pourquoi c'est instantané :** le serveur exporte les paramètres du modèle
  (`risk.model_params()` : moyennes, écarts-types, coefficients, constante). Le navigateur
  calcule p = 1 / (1 + e^−(b + Σ coef × (x − moyenne) / écart)) : aucune requête ni clé API.
- **Pourquoi c'est fiable :** un test compare ce calcul à `predict_proba` de scikit-learn sur
  50 cas aléatoires, à 10⁻⁵ près. Des contrôles jsdom exécutent le script sur la page rendue :
  le simulateur démarre exactement sur le risque enregistré.
- **Accessibilité :** chaque curseur a un libellé, une valeur lisible (`aria-valuetext`) et une
  annonce `aria-live` du risque, sans spam pendant qu'on fait glisser. Mise en page en une
  colonne sur mobile.

## Graphe interactif des dettes

Sur la page des soldes, le graphe SVG (sans bibliothèque) place les membres en cercle : vert
pour les créanciers, rouge pour les débiteurs. Une flèche signifie « doit à », et son épaisseur
est proportionnelle au montant. Une bascule passe des **dettes brutes** (paire par paire :
parts de dépenses M2 et remboursements confirmés, compensées dans les deux sens) aux
**virements simplifiés**. Dans la démo, on passe de 7 dettes à 4 virements (≤ n − 1). Cliquer
un membre (ou Tab puis Entrée) affiche ce qu'il doit et reçoit, et estompe le reste. Une version
texte liste les mêmes flèches pour les lecteurs d'écran.

Garanties testées : la somme des dettes brutes d'un membre est égale à son solde, et la
simplification conserve le solde de chacun avec au plus n − 1 virements (`services.pairwise_debts`,
`services.debt_graph`).

## Fonctionnalités

- **Graphique des soldes** (Chart.js) : barres horizontales vertes (créanciers) et rouges
  (débiteurs). Le tableau chiffré reste l'alternative accessible. Si le CDN est injoignable,
  le graphique est masqué et le tableau suffit.
- **Export de l'échéancier** : PDF (reportlab) avec les parties, les échéances, les retards
  et le risque ; calendrier `.ics` (RFC 5545) avec une journée par échéance non soldée et un
  rappel la veille, importable dans Google Agenda ou Outlook.

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
