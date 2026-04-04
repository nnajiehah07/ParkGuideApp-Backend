from django.contrib.auth import get_user_model
from .models import Notification, UserNotification

def create_notification_for_users(*,users,title,description="",full_text="",created_by=None,audience_type=Notification.AUDIENCE_SELECTED_GUIDES,tracking_type=Notification.TRACKING_INFO_ONLY,related_user=None):
    user_ids = list(users.values_list("id", flat=True))
    if not user_ids:
        return None, 0
    notification = Notification.objects.create(title=title,description=description,full_text=full_text or description or title,created_by=created_by,audience_type=audience_type,tracking_type=tracking_type,related_user=related_user)
    UserNotification.objects.bulk_create([UserNotification(user_id=user_id, notification=notification) for user_id in user_ids],ignore_conflicts=True)
    return notification, len(user_ids)

def create_notification_for_user(*,user,title,description="",full_text="",created_by=None,related_user=None):
    return create_notification_for_users(
        users=get_user_model().objects.filter(id=user.id),
        title=title,
        description=description,
        full_text=full_text,
        created_by=created_by,
        audience_type=Notification.AUDIENCE_SELECTED_GUIDES,
        tracking_type=Notification.TRACKING_INFO_ONLY,
        related_user=related_user or user,
    )

def create_notification_for_staff(*,title,description="",full_text="",created_by=None,related_user=None):
    User = get_user_model()
    staff_users = User.objects.filter(is_active=True, is_staff=True)
    return create_notification_for_users(
        users=staff_users,
        title=title,
        description=description,
        full_text=full_text,
        created_by=created_by,
        audience_type=Notification.AUDIENCE_ADMINS,
        tracking_type=Notification.TRACKING_ADMIN_SHARED,
        related_user=related_user,
    )