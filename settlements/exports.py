"""Exports d'un échéancier : PDF (reportlab) et calendrier iCalendar (.ics, RFC 5545)."""

from datetime import timedelta
from io import BytesIO

from django.utils import timezone


def _name(membership):
    return membership.user.get_full_name() or membership.user.username


def _money(value, currency):
    return f"{value:.3f} {currency}".replace(".", ",")


def plan_pdf(plan) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    settlement = plan.settlement
    currency = settlement.group.currency
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=f"Échéancier — {settlement.group.name}",
        author="Finova", leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Finova — Échéancier de remboursement", styles["Title"]),
        Paragraph(
            f"Groupe : <b>{settlement.group.name}</b><br/>"
            f"Payeur : <b>{_name(settlement.payer)}</b> → bénéficiaire : <b>{_name(settlement.receiver)}</b><br/>"
            f"Montant total : <b>{_money(plan.total_amount, currency)}</b> en {plan.installments_count} "
            f"échéances ({plan.get_frequency_display().lower()}) — statut : {plan.get_status_display().lower()}<br/>"
            f"Payé : {_money(plan.paid_amount, currency)} · reste : {_money(plan.remaining_amount, currency)}",
            styles["Normal"],
        ),
        Spacer(1, 0.6 * cm),
    ]
    rows = [["N°", "Échéance", "Montant", "Payé", "Statut"]]
    for inst in plan.installments.all():
        status = inst.get_status_display()
        if inst.is_late():
            status += f" (retard {inst.days_late()} j)"
        rows.append([
            str(inst.number), inst.due_date.strftime("%d/%m/%Y"),
            _money(inst.amount, currency), _money(inst.paid_amount, currency), status,
        ])
    table = Table(rows, colWidths=[1.2 * cm, 3 * cm, 3.5 * cm, 3.5 * cm, 5.5 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f5f59")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (2, 1), (3, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f1ea")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6d3d1")),
    ]))
    story.append(table)
    prediction = plan.latest_prediction()
    if prediction:
        story += [
            Spacer(1, 0.6 * cm),
            Paragraph(
                f"Risque de retard estimé : {prediction.percent} % ({prediction.get_label_display().lower()}) "
                f"— modèle {prediction.model_version}, entraîné sur un jeu de démonstration généré.",
                styles["Italic"],
            ),
        ]
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(f"Document généré le {timezone.localtime():%d/%m/%Y à %H:%M}.", styles["Normal"]))
    doc.build(story)
    return buffer.getvalue()


def _ics_escape(text):
    return (
        str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    )


def _fold(line):
    """Lignes de 75 octets au plus, les suivantes préfixées d'un espace (RFC 5545 §3.1)."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts, current = [], b""
    for char in line:
        piece = char.encode("utf-8")
        if len(current) + len(piece) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += piece
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def plan_ics(plan, host="finova.app") -> str:
    """Une journée entière par échéance non soldée, avec un rappel la veille."""
    settlement = plan.settlement
    currency = settlement.group.currency
    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Finova//Echeancier M3//FR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape('Finova — ' + settlement.group.name)}",
    ]
    for inst in plan.installments.exclude(status="paid"):
        summary = (
            f"Finova : échéance {inst.number}/{plan.installments_count} — "
            f"{_money(inst.remaining, currency)} à {_name(settlement.receiver)}"
        )
        description = (
            f"Remboursement de {_name(settlement.payer)} à {_name(settlement.receiver)} "
            f"(groupe {settlement.group.name}). Montant dû : {_money(inst.amount, currency)}, "
            f"déjà payé : {_money(inst.paid_amount, currency)}."
        )
        lines += [
            "BEGIN:VEVENT",
            f"UID:finova-installment-{inst.pk}@{host}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{inst.due_date:%Y%m%d}",
            f"DTEND;VALUE=DATE:{inst.due_date + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER:-P1D",
            f"DESCRIPTION:{_ics_escape(summary)}",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
