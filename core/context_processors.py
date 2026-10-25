def notifications(request):
    """Compteur de notifications non lues. Wassim (M6) branche le vrai modèle."""
    unread = 0
    if request.user.is_authenticated:
        try:
            from automation.models import Notification

            unread = Notification.objects.filter(
                user=request.user, is_read=False
            ).count()
        except Exception:
            unread = 0
    return {"unread_notifications": unread}
