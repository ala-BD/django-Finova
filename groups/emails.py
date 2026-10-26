"""E-mails du module groupes : invitation à rejoindre un groupe."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_invitation_email(invitation, accept_url: str, inviter) -> bool:
    """Envoie l'invitation (texte + HTML). Retourne False si l'envoi échoue : l'invitation
    reste valable et le lien est alors affiché à l'administrateur pour qu'il le transmette."""
    context = {
        "invitation": invitation,
        "group": invitation.group,
        "inviter": inviter.get_full_name() or inviter.username,
        "accept_url": accept_url,
        "expires_at": invitation.expires_at,
    }
    message = EmailMultiAlternatives(
        subject=f"{context['inviter']} vous invite à rejoindre « {invitation.group.name} » sur Finova",
        body=render_to_string("groups/emails/invitation.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[invitation.email],
        reply_to=[inviter.email] if inviter.email else None,
    )
    message.attach_alternative(render_to_string("groups/emails/invitation.html", context), "text/html")
    try:
        message.send(fail_silently=False)
        return True
    except Exception as exc:  # SMTP indisponible, identifiants refusés, réseau…
        logger.error("Envoi de l'invitation %s à %s impossible : %s", invitation.pk, invitation.email, exc)
        return False
