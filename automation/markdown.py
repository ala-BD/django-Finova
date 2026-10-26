"""Rendu Markdown minimal et sûr pour les réponses de l'assistant.

Tout le texte est échappé AVANT la mise en forme : une réponse du LLM ne peut donc jamais injecter
de HTML ni de script. Sous-ensemble géré : titres (#), gras, italique, `code`, listes à puces et
numérotées, tableaux à barres verticales, paragraphes et retours à la ligne. Pas de liens ni d'images.
"""

import html
import re

CODE_SPAN = re.compile(r"(`[^`\n]+`)")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
BULLET = re.compile(r"^\s*[-*•+]\s+(.*)$")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _inline(escaped: str) -> str:
    parts = CODE_SPAN.split(escaped)
    for index, part in enumerate(parts):
        if index % 2:  # morceau de code : aucune autre mise en forme
            parts[index] = f"<code>{part[1:-1]}</code>"
        else:
            part = BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", part)
            parts[index] = ITALIC.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", part)
    return "".join(parts)


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def render_markdown(text: str) -> str:
    lines = [html.escape(line, quote=True) for line in str(text or "").replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    paragraph: list[str] = []
    list_tag = None

    def close_paragraph():
        if paragraph:
            out.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Tableau : ligne d'en-tête suivie d'une ligne de séparation |---|
        if TABLE_ROW.match(line) and i + 1 < len(lines) and TABLE_SEPARATOR.match(lines[i + 1]):
            close_paragraph(), close_list()
            header = "".join(f'<th scope="col">{_inline(c)}</th>' for c in _cells(line))
            rows = []
            i += 2
            while i < len(lines) and TABLE_ROW.match(lines[i]):
                rows.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in _cells(lines[i])) + "</tr>")
                i += 1
            out.append(f'<div class="md-table"><table><thead><tr>{header}</tr></thead>'
                       f'<tbody>{"".join(rows)}</tbody></table></div>')
            continue

        heading = HEADING.match(stripped)
        bullet = BULLET.match(line)
        numbered = NUMBERED.match(line)
        if not stripped:
            close_paragraph(), close_list()
        elif heading:
            close_paragraph(), close_list()
            level = min(len(heading.group(1)) + 2, 6)  # # → h3 : jamais de h1/h2 dans une bulle
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet or numbered:
            close_paragraph()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_inline((bullet or numbered).group(1))}</li>")
        else:
            close_list()
            paragraph.append(stripped)
        i += 1

    close_paragraph()
    close_list()
    return "".join(out)
