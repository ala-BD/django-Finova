import calendar
import json
import logging
import re
from datetime import timedelta

from django.apps import apps
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from automation.models import (
    ChatMessage,
    Notification,
    RecurringExpense,
    RecurringOccurrence,
    ReminderDelivery,
    ReminderRule,
)
from automation.help import CAPABILITIES, HELP_TOPICS, build_help, help_markdown, topic_from_keywords
from core.ai import LLMError, call_llm
from groups.services import is_member

logger = logging.getLogger(__name__)

ASSISTANT_FUNCTIONS = {
    "get_balance": {
        "description": "Retourne les soldes calculés pour les groupes auxquels l'utilisateur appartient.",
        "parameters": {},
    },
    "list_expenses": {
        "description": "Liste les dépenses des groupes autorisés sur une période et catégorie.",
        "parameters": {
            "period": ["week", "month", "year", "all"],
            "category": "chaîne facultative, 50 caractères maximum",
        },
    },
    "get_budget_status": {
        "description": "Retourne le statut des budgets par catégorie des groupes de l'utilisateur.",
        "parameters": {"category": "chaîne obligatoire, 50 caractères maximum"},
    },
    "next_reminders": {
        "description": "Liste les échéances récurrentes de l'utilisateur pour les 30 prochains jours.",
        "parameters": {},
    },
    "summarize_month": {
        "description": "Résume les dépenses du mois courant dans les groupes autorisés.",
        "parameters": {},
    },
    "get_help": {
        "description": (
            "Explique comment utiliser l'application Finova (créer un groupe, inviter, ajouter une dépense, "
            "rembourser, confirmer, échéancier, soldes, dépenses récurrentes, analytics, budgets, quitter). "
            "À utiliser pour toute question « comment faire / où trouver », ou si aucune autre fonction ne convient "
            "(sujet vide = ce que l'assistant sait faire)."
        ),
        "parameters": {"topic": [*HELP_TOPICS, ""]},
    },
}


class AssistantRequestError(ValueError):
    pass


class IntegrationUnavailable(RuntimeError):
    pass


def notify(user, kind, message, link=None):
    if user is None or not getattr(user, "is_authenticated", False):
        raise ValueError("Une notification requiert un utilisateur authentifié.")
    if link and (not link.startswith("/") or link.startswith("//")):
        raise ValueError("Le lien d'une notification doit être un chemin interne.")
    return Notification.objects.create(
        user=user,
        kind=kind,
        message=message,
        link=link or "",
    )


def advance_date(current_date, frequency):
    if frequency == RecurringExpense.WEEKLY:
        return current_date + timedelta(days=7)
    if frequency == RecurringExpense.MONTHLY:
        year = current_date.year + (current_date.month == 12)
        month = 1 if current_date.month == 12 else current_date.month + 1
        day = min(current_date.day, calendar.monthrange(year, month)[1])
        return current_date.replace(year=year, month=month, day=day)
    if frequency == RecurringExpense.YEARLY:
        year = current_date.year + 1
        day = min(current_date.day, calendar.monthrange(year, current_date.month)[1])
        return current_date.replace(year=year, day=day)
    raise ValueError(f"Fréquence récurrente inconnue : {frequency}")


def _get_group_model():
    try:
        return apps.get_model("groups", "Group")
    except LookupError as exc:
        raise IntegrationUnavailable("Le modèle groups.Group de M1 doit être intégré.") from exc


def _authorized_groups(user):
    Group = _get_group_model()
    return [
        group
        for group in Group.objects.all()
        if is_member(user, group)
    ]


def _create_expense_from_occurrence(recurring, due_date):
    from expenses import services as expense_services

    create_expense = getattr(expense_services, "create_expense", None)
    if not callable(create_expense):
        raise IntegrationUnavailable(
            "Le contrat expenses.services.create_expense de M2 doit être intégré."
        )

    return create_expense(
        group_id=recurring.group_id,
        created_by=recurring.owner,
        title=recurring.title,
        description=recurring.description,
        amount=recurring.amount,
        category=recurring.category,
        incurred_on=due_date,
        recurring_expense_id=recurring.pk,
    )


def generate_recurring_expenses(today=None):
    today = today or timezone.localdate()
    generated_count = 0
    recurring_ids = list(RecurringExpense.objects.filter(
        status=RecurringExpense.ACTIVE,
        next_run__lte=today,
    ).values_list("pk", flat=True))

    for recurring_id in recurring_ids:
        while True:
            try:
                with transaction.atomic():
                    recurring = RecurringExpense.objects.select_for_update().get(
                        pk=recurring_id,
                        status=RecurringExpense.ACTIVE,
                    )
                    if recurring.next_run > today:
                        break

                    due_date = recurring.next_run
                    occurrence, created = RecurringOccurrence.objects.get_or_create(
                        recurring_expense=recurring,
                        due_date=due_date,
                    )
                    if created:
                        generated_expense = _create_expense_from_occurrence(
                            recurring,
                            due_date,
                        )
                        generated_expense_id = getattr(generated_expense, "pk", None)
                        if generated_expense_id is None:
                            raise IntegrationUnavailable(
                                "expenses.services.create_expense doit retourner la dépense créée."
                            )
                        occurrence.generated_expense_id = generated_expense_id
                        occurrence.save(update_fields=["generated_expense_id"])
                        notify(
                            recurring.owner,
                            "recurring_expense_generated",
                            f"La dépense récurrente « {recurring.title} » a été générée.",
                            f"/groups/{recurring.group_id}/recurring/{recurring.pk}/",
                        )
                        generated_count += 1

                    recurring.next_run = advance_date(due_date, recurring.frequency)
                    recurring.save(update_fields=["next_run"])
            except RecurringExpense.DoesNotExist:
                break
            except Exception as exc:
                logger.error(
                    "Échec de génération de la dépense récurrente id=%s : %s",
                    recurring_id,
                    exc,
                )
                break
    return generated_count


def send_due_reminders(today=None):
    today = today or timezone.localdate()
    rules = ReminderRule.objects.filter(
        enabled=True,
        recurring_expense__status=RecurringExpense.ACTIVE,
    ).select_related("recurring_expense", "recurring_expense__owner")
    sent_count = 0

    for rule in rules.iterator():
        recurring = rule.recurring_expense
        if recurring.next_run - timedelta(days=rule.days_before) != today:
            continue

        try:
            with transaction.atomic():
                locked_recurring = RecurringExpense.objects.select_for_update().get(
                    pk=recurring.pk,
                    status=RecurringExpense.ACTIVE,
                )
                locked_rule = ReminderRule.objects.select_for_update().get(pk=rule.pk)
                if locked_recurring.next_run - timedelta(days=locked_rule.days_before) != today:
                    continue
                if ReminderDelivery.objects.filter(
                    rule=locked_rule,
                    due_date=locked_recurring.next_run,
                ).exists():
                    continue

                message = (
                    f"La dépense « {locked_recurring.title} » est prévue le "
                    f"{locked_recurring.next_run:%d/%m/%Y}."
                )
                notify(
                    locked_recurring.owner,
                    "recurring_reminder",
                    message,
                    f"/groups/{locked_recurring.group_id}/recurring/{locked_recurring.pk}/",
                )
                if locked_rule.channel == ReminderRule.CHANNEL_EMAIL:
                    if not locked_recurring.owner.email:
                        raise ValueError(
                            f"L'utilisateur {locked_recurring.owner_id} n'a pas d'adresse email."
                        )
                    send_mail(
                        subject="Rappel de dépense récurrente — Finova",
                        message=message,
                        from_email=None,
                        recipient_list=[locked_recurring.owner.email],
                        fail_silently=False,
                    )
                elif locked_rule.channel != ReminderRule.CHANNEL_NOTIFICATION:
                    raise ValueError(f"Canal de rappel inconnu : {locked_rule.channel}")

                ReminderDelivery.objects.create(
                    rule=locked_rule,
                    due_date=locked_recurring.next_run,
                )
                locked_rule.last_sent_at = timezone.now()
                locked_rule.save(update_fields=["last_sent_at"])
                sent_count += 1
        except IntegrityError:
            logger.info(
                "Rappel déjà délivré rule=%s due_date=%s",
                rule.pk,
                rule.recurring_expense.next_run,
            )
        except Exception as exc:
            logger.error("Échec d'envoi du rappel rule=%s : %s", rule.pk, exc)
    return sent_count


def _parse_function_call(raw_result):
    if isinstance(raw_result, dict):
        result = raw_result
    elif isinstance(raw_result, str):
        # Les LLM entourent souvent le JSON de ```json … ``` : on extrait l'objet lui-même
        match = re.search(r"\{.*\}", raw_result, re.DOTALL)
        try:
            result = json.loads(match.group(0) if match else raw_result)
        except json.JSONDecodeError as exc:
            raise AssistantRequestError("Réponse de routage IA invalide.") from exc
    else:
        raise AssistantRequestError("Réponse de routage IA invalide.")

    if not isinstance(result, dict):
        raise AssistantRequestError("La réponse IA doit être un objet JSON.")
    function_name = result.get("function")
    arguments = result.get("arguments", {})
    if not isinstance(function_name, str) or function_name not in ASSISTANT_FUNCTIONS:
        raise AssistantRequestError("Fonction assistant inconnue.")
    if not isinstance(arguments, dict):
        raise AssistantRequestError("Les arguments de fonction doivent être un objet.")
    return function_name, arguments


def _validate_arguments(function_name, arguments):
    expected = {
        "get_balance": set(),
        "list_expenses": {"period", "category"},
        "get_budget_status": {"category"},
        "next_reminders": set(),
        "summarize_month": set(),
        "get_help": {"topic"},
    }[function_name]
    if not set(arguments).issubset(expected):
        raise AssistantRequestError("Argument non autorisé.")
    if function_name == "get_help":
        topic = arguments.get("topic") or ""
        return {"topic": topic if topic in HELP_TOPICS else ""}

    period = arguments.get("period", "month")
    if function_name == "list_expenses" and (
        not isinstance(period, str)
        or period not in {"week", "month", "year", "all"}
    ):
        raise AssistantRequestError("Période invalide.")
    for name in ("category",):
        value = arguments.get(name)
        if value is not None and (
            not isinstance(value, str) or len(value) > 50
        ):
            raise AssistantRequestError("Catégorie invalide.")
        if isinstance(value, str):
            arguments[name] = value.strip() or None
    if function_name == "get_budget_status" and not arguments.get("category", "").strip():
        raise AssistantRequestError("La catégorie du budget est obligatoire.")
    return {"period": period, **arguments}


HOW_TO_MARKERS = ("comment", "how ", "how?", "où ", "ou trouver", "where", "aide", "help",
                  "c'est quoi", "what is", "puis-je", "can i", "est-ce que je peux", "expliqu", "explain")


def _heuristic_function(question):
    """Routage par mots-clés (FR/EN), utilisé si le LLM est indisponible ou sa réponse inexploitable."""
    text = question.casefold()
    asks_amount = "combien" in text or "how much" in text  # « combien je dois » = donnée, pas mode d'emploi
    if not asks_amount and any(marker in text for marker in HOW_TO_MARKERS):
        return "get_help", {"topic": topic_from_keywords(text)}
    if any(word in text for word in ("budget",)):
        return "get_budget_status", {"category": "general"}
    if any(word in text for word in ("rappel", "échéance", "echeance", "remind", "upcoming", "due")):
        return "next_reminders", {}
    if any(word in text for word in ("solde", "dois", "rembourse", "owe", "balance", "debt")):
        return "get_balance", {}
    if any(word in text for word in ("résume", "resume", "résumé", "summary", "summarize", "summarise")):
        return "summarize_month", {}
    if any(word in text for word in ("dépense", "depense", "dépensé", "expense", "spend", "spent")):
        return "list_expenses", {"period": "month"}
    # Rien de reconnu : présenter ce que l'assistant sait faire plutôt que lister des dépenses au hasard
    return "get_help", {"topic": topic_from_keywords(text)}


def _route_question(user, question):
    schema = {
        "type": "object",
        "properties": {
            "function": {"type": "string", "enum": list(ASSISTANT_FUNCTIONS)},
            "arguments": {"type": "object"},
        },
        "required": ["function", "arguments"],
        "additionalProperties": False,
    }
    prompt = (
        "Choisis une seule fonction dans la liste blanche. Réponds uniquement en JSON "
        "avec les clés function et arguments. N'inclus aucun identifiant utilisateur "
        "ou groupe. La question peut être en français ou en anglais.\n"
        "- Question sur les données de l'utilisateur (combien, quelles dépenses, solde…) : "
        "la fonction de données correspondante.\n"
        "- Question « comment faire / où trouver / aide » sur l'application : get_help avec le sujet le plus proche.\n"
        "- Salutation, question hors sujet ou rien ne convient : get_help avec topic \"\".\n"
        f"Fonctions: {json.dumps(ASSISTANT_FUNCTIONS, ensure_ascii=False)}\nQuestion: {question}"
    )
    try:
        raw = call_llm(
            "assistant.route",
            prompt,
            schema=schema,
            module="automation",
            user=user,
        )
        return _parse_function_call(raw)
    except (LLMError, AssistantRequestError):
        # API indisponible OU réponse inexploitable : repli sur le routage par mots-clés
        return _heuristic_function(question)


def _serialize_expense(expense):
    return {
        "title": str(getattr(expense, "title", ""))[:120],
        "amount": str(getattr(expense, "amount", "")),
        "category": str(getattr(expense, "category", ""))[:50],
        "date": str(
            getattr(expense, "incurred_on", getattr(expense, "date", ""))
        ),
    }


def _execute_assistant_function(user, function_name, arguments):
    from budgets.services import get_budget_status
    from expenses.services import get_group_expenses
    from settlements.services import member_balance_summary

    groups = _authorized_groups(user)
    if function_name == "get_help":
        return build_help(arguments["topic"], groups)

    if function_name == "next_reminders":
        cutoff = timezone.localdate() + timedelta(days=30)
        group_ids = [group.pk for group in groups]
        rows = RecurringExpense.objects.filter(
            owner=user,
            group_id__in=group_ids,
            status=RecurringExpense.ACTIVE,
            next_run__lte=cutoff,
        ).values("title", "next_run", "amount", "category")
        return list(rows[:50])

    if function_name in {"list_expenses", "summarize_month"}:
        period = "month" if function_name == "summarize_month" else arguments["period"]
        category = arguments.get("category")
        if category == "all":
            category = None
        results = []
        for group in groups:
            expenses = get_group_expenses(group, period=period, category=category)
            results.extend(_serialize_expense(item) for item in expenses[:100])
        return results[:200]

    if function_name == "get_balance":
        # Solde de l'utilisateur connecté uniquement, avec des prénoms (contrat M3) : le LLM ne
        # reçoit plus les soldes de tous les membres indexés par identifiant interne.
        return [member_balance_summary(group, user) for group in groups]

    if function_name == "get_budget_status":
        month = timezone.localdate().replace(day=1)
        category = arguments["category"]
        return [
            get_budget_status(group, category, month)
            for group in groups
        ]

    raise AssistantRequestError("Fonction assistant inconnue.")


def _format_answer(user, question, function_name, data):
    prompt = (
        "Tu es l'assistant de Finova, une application de partage de dépenses. Réponds dans la langue de la "
        "question : si elle est en anglais, réponds entièrement en anglais et traduis les informations "
        "(garde seulement les libellés de boutons entre guillemets en français, car l'interface est en français). "
        "Sois clair, concis et chaleureux, en Markdown simple (gras, listes, étapes numérotées).\n"
        "Règles :\n"
        "- Appuie-toi uniquement sur les informations ci-dessous ; n'invente ni montant, ni nom, ni lien.\n"
        "- Ne parle jamais de JSON, de fonction ni de données techniques.\n"
        "- Pour un lien, reprends exactement une url fournie, au format [texte](url).\n"
        "- Si les informations ne permettent pas de répondre, dis-le simplement et propose ce que tu sais faire : "
        f"{', '.join(CAPABILITIES)}.\n"
        f"Question : {question}\n"
        f"Informations : {json.dumps(data, ensure_ascii=False, default=str)}"
    )
    try:
        answer = call_llm(
            "assistant.answer",
            prompt,
            module="automation",
            user=user,
        )
        if isinstance(answer, dict):
            answer = answer.get("answer", "")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
    except LLMError:
        logger.warning("LLM indisponible pour formuler la réponse assistant.")

    if function_name == "get_help":
        return help_markdown(data)
    if not data:
        return "Je n'ai trouvé aucune donnée correspondante dans vos groupes."
    return "Voici les résultats disponibles : " + json.dumps(
        data,
        ensure_ascii=False,
        default=str,
    )


def answer_assistant_question(user, question):
    function_name, raw_arguments = _route_question(user, question)
    arguments = _validate_arguments(function_name, raw_arguments)
    data = _execute_assistant_function(user, function_name, arguments)
    answer = _format_answer(user, question, function_name, data)
    return ChatMessage.objects.create(
        user=user,
        question=question,
        answer=answer,
    )
