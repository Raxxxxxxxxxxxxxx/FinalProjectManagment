def notification_summary(request):
    if not request.user.is_authenticated:
        return {}
    queryset = request.user.notifications.select_related("rfq", "order")
    return {
        "unread_notification_count": queryset.filter(is_read=False).count(),
        "latest_notifications": queryset[:5],
    }
