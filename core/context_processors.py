def notifications(request):
    """Compteur des notifications non lues de l'utilisateur courant."""
    unread = 0
    if request.user.is_authenticated:
        from automation.models import Notification

        unread = Notification.objects.filter(user=request.user, is_read=False).count()
    return {"unread_notifications": unread}
