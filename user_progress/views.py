from rest_framework import permissions, viewsets

from .models import Badge, UserBadge
from .serializers import BadgeStatusSerializer, UserBadgeSerializer
from .services import ensure_badge_rows_for_user, sync_user_badges, get_user_requirement_progress_for_badge


class BadgeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BadgeStatusSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Badge.objects.filter(is_active=True).select_related('course')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        user = self.request.user
        ensure_badge_rows_for_user(user)
        sync_user_badges(user)
        badges = list(self.get_queryset())

        status_rows = UserBadge.objects.filter(
            user=user,
            badge_id__in=[badge.id for badge in badges],
        ).values_list('badge_id', 'status')
        status_map = {badge_id: status for badge_id, status in status_rows}

        granted_regular_badges = user.badge_progress.filter(
            status=UserBadge.STATUS_GRANTED,
            badge__is_major_badge=False,
        ).count()

        completed_count_map = {}
        completed_badge_count_map = {}
        for badge in badges:
            if badge.is_major_badge:
                completed_count_map[badge.id] = 0
                completed_badge_count_map[badge.id] = granted_regular_badges
                continue
            completed_count, _ = get_user_requirement_progress_for_badge(badge, user)
            completed_count_map[badge.id] = completed_count
            completed_badge_count_map[badge.id] = 0

        context['status_map'] = status_map
        context['completed_count_map'] = completed_count_map
        context['completed_badge_count_map'] = completed_badge_count_map
        return context


class MyBadgeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserBadgeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        ensure_badge_rows_for_user(self.request.user)
        sync_user_badges(self.request.user)
        return UserBadge.objects.filter(
            user=self.request.user,
            status=UserBadge.STATUS_GRANTED,
            is_awarded=True,
        ).select_related('badge', 'badge__course')
