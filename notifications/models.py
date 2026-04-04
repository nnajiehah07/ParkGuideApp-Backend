from django.conf import settings
from django.db import models


class Notification(models.Model):
    AUDIENCE_ADMINS = 'admins'
    AUDIENCE_ALL_GUIDES = 'all_guides'
    AUDIENCE_SELECTED_GUIDES = 'selected_guides'
    AUDIENCE_CHOICES = [
        (AUDIENCE_ADMINS, 'Admins'),
        (AUDIENCE_ALL_GUIDES, 'All Guides'),
        (AUDIENCE_SELECTED_GUIDES, 'Selected Guides'),
    ]
    TRACKING_ADMIN_SHARED = 'admin_shared'
    TRACKING_INFO_ONLY = 'info_only'
    TRACKING_USER_READ = 'user_read'
    TRACKING_USER_ACK = 'user_ack'
    TRACKING_CHOICES = [
        (TRACKING_ADMIN_SHARED, 'Shared admin read'),
        (TRACKING_INFO_ONLY, 'Info only'),
        (TRACKING_USER_READ, 'User read tracking'),
        (TRACKING_USER_ACK, 'User acknowledgement'),
    ]
    title = models.CharField(max_length=200)
    description = models.CharField(max_length=255, blank=True)
    full_text = models.TextField()
    audience_type = models.CharField(max_length=32, choices=AUDIENCE_CHOICES, default=AUDIENCE_ALL_GUIDES)
    tracking_type = models.CharField(max_length=32, choices=TRACKING_CHOICES,default=TRACKING_INFO_ONLY)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_notifications')
    related_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='related_notifications')
    sent_at = models.DateTimeField(auto_now_add=True)
    # once any admin reads it, all admins see it as read
    admin_seen_at = models.DateTimeField(null=True, blank=True)
    admin_seen_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='admin_seen_notifications')
    show_in_header = models.BooleanField(default=True)

    class Meta:
        ordering = ('-sent_at',)

    def __str__(self):
        return self.title

class UserNotification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name='recipients')
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'notification')
        ordering = ('-notification__sent_at',)

    def __str__(self):
        return f'{self.user} - {self.notification}'