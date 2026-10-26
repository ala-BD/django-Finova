"""
Rapport mensuel des budgets en PDF (reportlab) et envoi par email.

Le PDF reprend, pour un groupe et un mois : la consommation de chaque budget
et, pour le mois en cours, l'analyse IA (prévision, fourchette, risque).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from django.utils.formats import date_format

from . import services

LEVEL_TEXT = {
    "ok": "Dans les clous",
    "warning": "Attention (80 %)",
    "over": "Dépassé",
    "none": "-",
}


def _money(value, currency: str = "") -> str:
    if value is None:
        return "-"
    text = f"{Decimal(value):,.3f}".replace(",", " ").replace(".", ",")
    return f"{text} {currency}".strip()


def report_filename(group, month: date) -> str:
    return f"finova-budgets-groupe{group.pk}-{month:%Y-%m}.pdf"


def build_budget_report(group, month: date, user=None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    month = services.first_of_month(month)
    statuses = services.group_budget_statuses(group, month)
    is_current = month == services.current_month()
    analyses = {}
    if is_current:
        for status in statuses:
            analyses[status["budget"].pk] = services.forecast_budget(status["budget"], user=user, save=False)

    buffer = BytesIO()
    title = f"Budgets de {date_format(month, 'F Y')} - {group.name}"
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=title, author="Finova",
        leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.6 * cm, bottomMargin=1.6 * cm,
    )
    styles = getSampleStyleSheet()
    accent = colors.HexColor("#0f5f59")
    story = [
        Paragraph(f"<font color='#0f5f59'><b>Finova</b></font> - rapport de budgets", styles["Title"]),
        Paragraph(f"<b>Groupe :</b> {group.name} &nbsp;&nbsp; <b>Mois :</b> {date_format(month, 'F Y')}", styles["Normal"]),
        Paragraph(
            f"Généré le {date_format(timezone.localtime(), 'j F Y à H:i')}"
            + (f" par {user.get_full_name() or user.username}" if user else ""),
            styles["Normal"],
        ),
        Spacer(1, 0.5 * cm),
    ]

    total_limit = sum((s["effective_limit"] for s in statuses), Decimal("0"))
    total_spent = sum((s["consumed"] for s in statuses), Decimal("0"))
    summary = Table(
        [["Total budgété", "Total dépensé", "Budgets suivis"],
         [_money(total_limit, group.currency), _money(total_spent, group.currency), str(len(statuses))]],
        colWidths=[16.7 * cm / 3] * 3,
    )
    summary.setStyle(TableStyle([
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#44403c")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 13),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6d3d1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6d3d1")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [summary, Spacer(1, 0.6 * cm), Paragraph("Consommation par catégorie", styles["Heading2"])]

    if statuses:
        rows = [["Catégorie", "Limite", "Dépensé", "Reste", "%", "Statut"]]
        for s in statuses:
            rows.append([
                s["category"].name,
                _money(s["effective_limit"]),
                _money(s["consumed"]),
                _money(s["remaining"]),
                f"{s['percent']:.1f}".replace(".", ","),
                LEVEL_TEXT[s["level"]],
            ])
        table = Table(rows, colWidths=[4 * cm, 2.6 * cm, 2.6 * cm, 2.6 * cm, 1.6 * cm, 3.3 * cm], repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), accent),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (4, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6d3d1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f1ea")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        for index, s in enumerate(statuses, start=1):
            if s["level"] == "over":
                style.append(("TEXTCOLOR", (5, index), (5, index), colors.HexColor("#991b1b")))
            elif s["level"] == "warning":
                style.append(("TEXTCOLOR", (5, index), (5, index), colors.HexColor("#854d0e")))
        table.setStyle(TableStyle(style))
        story.append(table)
    else:
        story.append(Paragraph("Aucun budget défini pour ce mois.", styles["Normal"]))

    if analyses:
        story += [Spacer(1, 0.6 * cm), Paragraph("Analyse IA (mois en cours)", styles["Heading2"])]
        rows = [["Catégorie", "Prévision fin de mois", "Fourchette 80 %", "Risque"]]
        for s in statuses:
            a = analyses[s["budget"].pk]
            interval = f"{_money(a['interval_low'])} - {_money(a['interval_high'])}" if a["interval_low"] else "-"
            risk = f"{a['risk_percent']} % ({a['risk_label']})" if a["risk_percent"] is not None else "-"
            rows.append([s["category"].name, _money(a["predicted_total"]), interval, risk])
        table = Table(rows, colWidths=[4 * cm, 4 * cm, 4.9 * cm, 3.8 * cm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4c1d95")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (2, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d6d3d1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f3ff")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story += [
            table,
            Spacer(1, 0.3 * cm),
            Paragraph(
                "Prévision : déjà dépensé + reste habituel des mois précédents. Risque : modèle de gradient "
                "boosting (scikit-learn) entraîné sur des données simulées, F1 0,83 sur le jeu de test.",
                styles["Italic"],
            ),
        ]

    doc.build(story)
    return buffer.getvalue()


def email_budget_report(group, month: date, user) -> None:
    """Envoie le rapport PDF en pièce jointe à l'adresse de l'utilisateur (backend email de Django)."""
    month = services.first_of_month(month)
    pdf = build_budget_report(group, month, user=user)
    label = date_format(month, "F Y")
    message = EmailMessage(
        subject=f"Finova · budgets de {label} ({group.name})",
        body=(
            f"Bonjour {user.get_short_name() or user.username},\n\n"
            f"Vous trouverez en pièce jointe le rapport des budgets du groupe « {group.name} » pour {label}.\n\n"
            "— Finova"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach(report_filename(group, month), pdf, "application/pdf")
    message.send(fail_silently=False)
