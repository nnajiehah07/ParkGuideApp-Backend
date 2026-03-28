from django.contrib.auth import get_user_model

from .models import Notification, UserNotification


def create_notification_for_users(*, users, title, description="", full_text="", created_by=None):
    user_ids = list(users.values_list("id", flat=True))
    if not user_ids:
        return None, 0

    notification = Notification.objects.create(
        title=title,
        description=description,
        full_text=full_text or description or title,
        created_by=created_by,
    )
    UserNotification.objects.bulk_create(
        [UserNotification(user_id=user_id, notification=notification) for user_id in user_ids],
        ignore_conflicts=True,
    )
    return notification, len(user_ids)


def create_notification_for_user(*, user, title, description="", full_text="", created_by=None):
    return create_notification_for_users(
        users=get_user_model().objects.filter(id=user.id),
        title=title,
        description=description,
        full_text=full_text,
        created_by=created_by,
    )


def create_notification_for_staff(*, title, description="", full_text="", created_by=None):
    User = get_user_model()
    staff_users = User.objects.filter(is_active=True, is_staff=True)
    return create_notification_for_users(
        users=staff_users,
        title=title,
        description=description,
        full_text=full_text,
        created_by=created_by,
    )
