"""Exports CSV (module csv) et PDF (reportlab, Python pur)."""

from __future__ import annotations

import csv
import io

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from analytics.services import format_money as _format_money
from analytics.services import format_number as _format_number
from analytics.services import total, totals_by

# Espace fine insécable de format_number : absente des polices standard du PDF.
NARROW_NBSP = chr(0x202F)

CSV_HEADER = ["Date", "Groupe", "Libellé", "Catégorie", "Payeur", "Montant", "Devise"]


def build_csv(records) -> bytes:
    """CSV au format attendu par Excel en français : séparateur « ; », UTF-8 avec BOM."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(CSV_HEADER)
    for record in sorted(records, key=lambda r: (r.date, r.id)):
        writer.writerow(
            [
                record.date.isoformat(),
                record.group_name,
                record.title,
                record.category,
                record.payer,
                f"{record.amount:.3f}".replace(".", ","),
                record.currency,
            ]
        )
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def build_pdf(records, *, title: str, description: str, summary: str, currency: str) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=title,
        author="Finova",
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(_escape(title), styles["Title"]),
        Paragraph(
            _escape(f"Filtre : {description} · généré le {timezone.localtime():%d/%m/%Y à %H:%M}"),
            styles["Normal"],
        ),
        Spacer(1, 0.4 * cm),
    ]
    if summary:
        story += [Paragraph("Résumé du mois", styles["Heading2"]), Paragraph(_escape(_pdf_text(summary)), styles["Normal"])]

    grand_total = total(records)
    story += [
        Spacer(1, 0.4 * cm),
        Paragraph("Totaux par catégorie", styles["Heading2"]),
        _table(
            [["Catégorie", "Montant", "Part"]]
            + [
                [name, format_money(value, currency), f"{format_number(value / grand_total * 100, 1)} %"]
                for name, value in totals_by(records, lambda r: r.category)
            ]
            + [["Total", format_money(grand_total, currency), "100 %"]],
            col_widths=[7 * cm, 5 * cm, 3 * cm],
            total_row=True,
        )
        if records
        else Paragraph("Aucune dépense pour ce filtre.", styles["Normal"]),
        Spacer(1, 0.4 * cm),
        Paragraph(f"Détail des dépenses ({len(records)})", styles["Heading2"]),
    ]
    if records:
        rows = [["Date", "Libellé", "Catégorie", "Payeur", "Montant"]]
        for record in sorted(records, key=lambda r: (r.date, r.id)):
            rows.append(
                [
                    f"{record.date:%d/%m/%Y}",
                    Paragraph(_escape(record.title), styles["BodyText"]),
                    record.category,
                    record.payer,
                    format_money(record.amount, record.currency),
                ]
            )
        story.append(_table(rows, col_widths=[2.3 * cm, 5.2 * cm, 2.8 * cm, 2.7 * cm, 3.4 * cm]))
    doc.build(story)
    return buffer.getvalue()


def format_money(value, currency):
    return _pdf_text(_format_money(value, currency))


def format_number(value, places=3):
    return _pdf_text(_format_number(value, places))


def _table(rows, col_widths, total_row=False):
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f5f59")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d6d3d1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f1ea")]),
    ]
    if total_row:
        style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return table


def _pdf_text(text: str) -> str:
    return text.replace(NARROW_NBSP, " ")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
