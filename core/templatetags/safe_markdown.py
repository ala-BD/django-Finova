from django import template
from django.utils.safestring import mark_safe

from core.markdown import render_markdown

register = template.Library()


@register.filter(name="markdown")
def markdown_filter(text):
    # Sûr : render_markdown échappe tout le texte avant d'ajouter ses propres balises
    return mark_safe(render_markdown(text))  # noqa: S308
