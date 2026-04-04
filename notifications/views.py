from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Notification, UserNotification
from .serializers import UserNotificationSerializer

class UserNotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserNotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return UserNotification.objects.filter(user=self.request.user).select_related('notification')

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        user_notification = self.get_object()
        now = timezone.now()
        if not user_notification.is_read:
            user_notification.is_read = True
            user_notification.read_at = now
            user_notification.save(update_fields=['is_read', 'read_at'])
        # if any admin reads it, it counts as read for all admins
        if request.user.is_staff:
            Notification.objects.filter(id=user_notification.notification_id).update(admin_seen_at=now, admin_seen_by=request.user)
        return Response(self.get_serializer(user_notification).data)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        now = timezone.now()
        unread_qs = self.get_queryset().filter(is_read=False)
        notification_ids = list(unread_qs.values_list('notification_id', flat=True).distinct())
        updated = unread_qs.update(is_read=True, read_at=now)
        if request.user.is_staff and notification_ids:
            Notification.objects.filter(id__in=notification_ids).update(admin_seen_at=now, admin_seen_by=request.user)
        return Response({'updated': updated}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='clear-read')
    def clear_read(self, request):
        deleted, _ = self.get_queryset().filter(is_read=True).delete()
        return Response({'deleted': deleted}, status=status.HTTP_200_OK)