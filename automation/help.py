"""Guide d'utilisation de Finova pour l'assistant (fonction get_help).

Contenu statique, rédigé par l'équipe : l'assistant ne l'invente pas. Les liens sont des chemins
internes, complétés avec les groupes de l'utilisateur connecté (jamais ceux d'un autre).
"""

HELP_TOPICS = {
    "creer_groupe": {
        "titre": "Créer un groupe",
        "mots_cles": ["créer un groupe", "creer un groupe", "nouveau groupe", "ajouter un groupe",
                      "create a group", "add a group", "new group"],
        "etapes": [
            "Ouvrez « Groupes » dans le menu.",
            "Cliquez sur « Créer manuellement » : nom (3 à 80 caractères), devise et règle de partage.",
            "Ou cliquez sur « Créer avec l'IA » et décrivez votre projet en une phrase "
            "(ex. « voyage de 5 amis à Djerba, budget 2 000 TND ») : le groupe et ses catégories sont proposés.",
            "Vous devenez automatiquement administrateur du groupe.",
        ],
        "liens": [("Créer un groupe", "/groups/new/"), ("Créer avec l'IA", "/groups/new/ai/")],
    },
    "inviter": {
        "titre": "Inviter des membres",
        "mots_cles": ["inviter", "invitation", "ajouter un membre", "invite", "add a member", "add member"],
        "etapes": [
            "Ouvrez le groupe (réservé aux administrateurs).",
            "Cliquez sur « Inviter » et saisissez l'e-mail de la personne.",
            "Elle reçoit un lien valable 7 jours ; en l'ouvrant connectée, elle rejoint le groupe.",
        ],
        "lien_groupe": ("Inviter dans {groupe}", "/groups/{gid}/invite/"),
    },
    "ajouter_depense": {
        "titre": "Ajouter une dépense",
        "mots_cles": ["dépense", "depense", "ajouter une dépense", "ticket", "payé", "add an expense",
                      "add expense", "expense", "receipt"],
        "etapes": [
            "Ouvrez le groupe puis « Dépenses ».",
            "Cliquez sur « Nouvelle dépense » : titre, montant, date, payeur, catégorie et règle de partage.",
            "Pour un ticket détaillé, ajoutez les lignes : chacun réclame ses articles, taxe et pourboire "
            "sont répartis au prorata.",
            "Les parts de chacun sont calculées automatiquement et mettent à jour les soldes.",
        ],
        "lien_groupe": ("Nouvelle dépense dans {groupe}", "/groups/{gid}/expenses/new/"),
    },
    "rembourser": {
        "titre": "Rembourser quelqu'un",
        "mots_cles": ["rembourser", "remboursement", "rendre l'argent", "payer quelqu'un", "virement",
                      "refund", "repay", "pay back", "reimburse", "settle"],
        "etapes": [
            "Ouvrez le groupe puis « Soldes » : la page indique le minimum de virements à faire.",
            "Cliquez sur « Rembourser » à côté d'un virement vous concernant (montant pré-rempli), "
            "ou sur « Déclarer un remboursement ».",
            "Astuce : décrivez-le en une phrase (« j'ai remboursé 30 dinars à Ala en espèces »), "
            "l'IA remplit le formulaire.",
            "Le remboursement reste « en attente » jusqu'à ce que le bénéficiaire confirme la réception ; "
            "il compte alors dans les soldes.",
        ],
        "lien_groupe": ("Déclarer un remboursement dans {groupe}", "/groups/{gid}/settlements/new/"),
    },
    "confirmer": {
        "titre": "Confirmer ou rejeter un remboursement reçu",
        "mots_cles": ["confirmer", "valider un remboursement", "rejeter", "reçu", "confirm", "reject", "received"],
        "etapes": [
            "Ouvrez le groupe puis « Remboursements » : le nombre de remboursements à confirmer s'affiche.",
            "Ouvrez le remboursement et cliquez sur « Confirmer la réception » ou « Rejeter ».",
            "Seul le bénéficiaire peut confirmer, et la confirmation est définitive.",
        ],
        "lien_groupe": ("Remboursements de {groupe}", "/groups/{gid}/settlements/"),
    },
    "echeancier": {
        "titre": "Rembourser en plusieurs fois (échéancier)",
        "mots_cles": ["échéancier", "echeancier", "plusieurs fois", "mensualité", "échéance", "installment",
                      "instalment", "payment plan", "several times"],
        "etapes": [
            "Ouvrez le remboursement en attente puis « Créer un échéancier ».",
            "Choisissez le nombre d'échéances (2 à 24), la fréquence et la première date ; "
            "le bouton « Suggérer » propose l'échéancier le moins risqué selon votre budget mensuel.",
            "Chaque échéance peut être payée en plusieurs fois ; le risque de retard est estimé par l'IA "
            "et le simulateur montre l'effet de chaque choix.",
            "L'échéancier s'exporte en PDF ou dans votre agenda (.ics).",
        ],
        "lien_groupe": ("Remboursements de {groupe}", "/groups/{gid}/settlements/"),
    },
    "soldes": {
        "titre": "Comprendre les soldes",
        "mots_cles": ["solde", "qui doit", "combien je dois", "balance", "who owes", "how much do i owe"],
        "etapes": [
            "Solde = ce que vous avez payé − votre part des dépenses + remboursements confirmés.",
            "Positif : on vous doit de l'argent ; négatif : vous devez de l'argent.",
            "La page « Soldes » propose au plus n − 1 virements pour tout solder, avec un graphe interactif.",
        ],
        "lien_groupe": ("Soldes de {groupe}", "/groups/{gid}/balances/"),
    },
    "recurrentes": {
        "titre": "Dépenses récurrentes et rappels",
        "mots_cles": ["récurrent", "recurrent", "loyer", "abonnement", "rappel", "recurring", "subscription",
                      "rent", "reminder"],
        "etapes": [
            "Ouvrez « Dépenses récurrentes » du groupe et créez la dépense (montant, fréquence, prochaine date).",
            "Elle est générée automatiquement à chaque échéance.",
            "Ajoutez une règle de rappel (de 0 à 30 jours avant) par notification ou par e-mail.",
        ],
        "lien_groupe": ("Dépenses récurrentes de {groupe}", "/groups/{gid}/recurring/"),
    },
    "analytics": {
        "titre": "Tableau de bord et rapports",
        "mots_cles": ["analytics", "tableau de bord", "graphique", "rapport", "export", "csv", "pdf",
                      "anomalie", "dashboard", "report", "chart"],
        "etapes": [
            "Ouvrez « Analytics » dans le menu : répartition par catégorie, évolution, dépenses par membre.",
            "Enregistrez des filtres et générez des rapports CSV ou PDF.",
            "La détection d'anomalies signale les dépenses inhabituelles et les doublons.",
        ],
        "liens": [("Analytics", "/analytics/"), ("Rapports", "/analytics/reports/")],
    },
    "budgets": {
        "titre": "Budgets",
        "mots_cles": ["budget", "limite", "plafond", "limit"],
        "etapes": [
            "Le module Budgets (catégories, limites mensuelles, alertes à 80 % et 100 %, simulateur) "
            "est en cours de finalisation par l'équipe.",
        ],
        "liens": [("Budgets", "/budgets/")],
    },
    "quitter": {
        "titre": "Quitter un groupe",
        "mots_cles": ["quitter", "partir", "leave", "quit"],
        "etapes": [
            "Ouvrez le groupe et cliquez sur « Quitter ».",
            "C'est possible seulement si votre solde est à zéro et si vous n'êtes pas le dernier administrateur.",
        ],
        "lien_groupe": ("Quitter {groupe}", "/groups/{gid}/leave/"),
    },
}

CAPABILITIES = [
    "vos soldes et à qui vous devez de l'argent",
    "vos dépenses (semaine, mois, année)",
    "vos prochaines échéances récurrentes",
    "un résumé de votre mois",
    "comment utiliser Finova : créer un groupe, ajouter une dépense, rembourser, confirmer, échéancier…",
]


def topic_from_keywords(question: str) -> str:
    text = question.casefold()
    best, best_length = "", 0
    for topic, data in HELP_TOPICS.items():
        for keyword in data["mots_cles"]:
            if keyword in text and len(keyword) > best_length:
                best, best_length = topic, len(keyword)
    return best


def build_help(topic: str, groups) -> dict:
    """Aide sur un sujet (ou vue d'ensemble), avec des liens vers les groupes de l'utilisateur."""
    data = HELP_TOPICS.get(topic)
    if data is None:
        return {
            "sujet": "vue d'ensemble",
            "je_peux_repondre_sur": CAPABILITIES,
            "sujets_d_aide": [d["titre"] for d in HELP_TOPICS.values()],
        }
    links = [{"texte": text, "url": url} for text, url in data.get("liens", [])]
    if "lien_groupe" in data:
        text, url = data["lien_groupe"]
        links += [
            {"texte": text.format(groupe=group.name), "url": url.format(gid=group.pk)}
            for group in list(groups)[:5]
        ]
        if not groups:
            links.append({"texte": "Créer d'abord un groupe", "url": "/groups/new/"})
    return {"sujet": data["titre"], "etapes": data["etapes"], "liens": links}


def help_markdown(help_data: dict) -> str:
    """Réponse rédigée sans LLM (repli) à partir de l'aide."""
    if "etapes" not in help_data:
        lines = ["Je peux vous renseigner sur :"]
        lines += [f"- {item}" for item in help_data["je_peux_repondre_sur"]]
        return "\n".join(lines)
    lines = [f"### {help_data['sujet']}"]
    lines += [f"{index}. {step}" for index, step in enumerate(help_data["etapes"], 1)]
    if help_data["liens"]:
        lines.append("")
        lines += [f"- [{link['texte']}]({link['url']})" for link in help_data["liens"]]
    return "\n".join(lines)
