from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.utils import timezone

from park_guide.admin_mixins import DashboardStatsChangeListMixin

from .models import Notification, UserNotification


@admin.register(Notification)
class NotificationAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = ('id', 'title', 'sent_at', 'created_by')
    search_fields = ('title', 'description', 'full_text')
    readonly_fields = ('sent_at',)
    actions = ('send_to_all_users',)
    dashboard_title = 'Communications'
    dashboard_description = 'Create announcements and keep outreach to park guide users organized.'

    def _deliver_to_app_users(self, notifications_queryset):
        User = get_user_model()
        users = User.objects.filter(is_active=True, is_staff=False, is_superuser=False)

        user_ids = list(users.values_list('id', flat=True))
        notification_ids = list(notifications_queryset.values_list('id', flat=True))

        if not user_ids or not notification_ids:
            return 0, len(user_ids)

        existing_pairs = set(
            UserNotification.objects.filter(
                user_id__in=user_ids,
                notification_id__in=notification_ids,
            ).values_list('user_id', 'notification_id')
        )

        to_create = []
        for notification_id in notification_ids:
            for user_id in user_ids:
                pair = (user_id, notification_id)
                if pair in existing_pairs:
                    continue
                to_create.append(UserNotification(user_id=user_id, notification_id=notification_id))

        UserNotification.objects.bulk_create(to_create, ignore_conflicts=True)
        return len(to_create), len(user_ids)

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

        if not change:
            created_count, user_count = self._deliver_to_app_users(Notification.objects.filter(id=obj.id))
            self.message_user(
                request,
                f'Notification sent immediately to {user_count} app users. Created {created_count} delivery entries.',
                level=messages.SUCCESS,
            )

    @admin.action(description='Send selected notifications to all app users')
    def send_to_all_users(self, request, queryset):
        created_count, user_count = self._deliver_to_app_users(queryset)

        self.message_user(
            request,
            f'Sent notifications to {user_count} app users. Created {created_count} new user notification entries.',
            level=messages.SUCCESS,
        )

    def get_dashboard_stats(self, request, queryset):
        today = timezone.localdate()
        return [
            {'label': 'Messages', 'value': queryset.count()},
            {'label': 'Created today', 'value': queryset.filter(sent_at__date=today).count()},
            {'label': 'Delivery rows', 'value': UserNotification.objects.count()},
        ]


@admin.register(UserNotification)
class UserNotificationAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = ('id', 'user', 'notification', 'is_read', 'read_at')
    list_filter = ('is_read', 'read_at')
    search_fields = ('user__email', 'user__username', 'notification__title')
    autocomplete_fields = ('user', 'notification')
    ordering = ('-notification__sent_at',)
    dashboard_title = 'Notification Delivery'
    dashboard_description = 'Follow read status and see which alerts still have not been opened.'

    def get_dashboard_stats(self, request, queryset):
        total = queryset.count()
        unread = queryset.filter(is_read=False).count()
        return [
            {'label': 'Visible deliveries', 'value': total},
            {'label': 'Unread', 'value': unread},
            {'label': 'Read', 'value': total - unread},
        ]
