import requests
import logging
from django.contrib.auth import get_user_model
from .models import Notification, UserNotification, PushToken

logger = logging.getLogger(__name__)

# Expo Push Notification Service
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

def send_push_notification(tokens, title, body, data=None):
    """
    Send push notification to one or more Expo push tokens
    
    Args:
        tokens: Single token (str) or list of tokens
        title: Notification title
        body: Notification body
        data: Optional dict of additional data to include
    
    Returns:
        Response from Expo API or error dict
    """
    print(f"\n=== SEND_PUSH_NOTIFICATION CALLED ===")
    print(f"Tokens: {tokens}")
    
    if isinstance(tokens, str):
        tokens = [tokens]
    
    if not tokens:
        print("No tokens provided")
        logger.warning("send_push_notification called with no tokens")
        return {'error': 'No tokens provided'}
    
    print(f"Sending push notification to {len(tokens)} tokens")
    logger.info(f"Sending push notification to {len(tokens)} tokens")
    
    # Build push notification payload
    messages = []
    for token in tokens:
        message = {
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "badge": 1,
            "priority": "high",
        }
        if data:
            message["data"] = data
        messages.append(message)
    
    try:
        print(f"Posting to Expo API URL: {EXPO_PUSH_URL}")
        print(f"Message count: {len(messages)}")
        logger.info(f"Posting to Expo API with {len(messages)} messages")
        
        response = requests.post(EXPO_PUSH_URL, json=messages)
        print(f"Expo response status: {response.status_code}")
        logger.info(f"Expo response status: {response.status_code}")
        
        result = response.json()
        print(f"Expo response: {result}")
        logger.info(f"Expo response: {result}")
        return result
    except Exception as e:
        print(f"Error sending push notification: {str(e)}")
        logger.error(f"Error sending push notification: {str(e)}", exc_info=True)
        return {'error': str(e)}


def send_push_to_users(users, title, description, data=None):
    """
    Send push notification to specific users via their registered devices
    
    Args:
        users: Django User QuerySet or list
        title: Notification title
        description: Notification body/description
        data: Optional dict of additional data
    
    Returns:
        Dict with push results
    """
    print(f"\n=== SEND_PUSH_TO_USERS CALLED ===")
    print(f"Users count: {len(users) if isinstance(users, list) else 'queryset'}")
    logger.info(f"send_push_to_users called with {len(users) if isinstance(users, list) else 'queryset'} users")
    
    if not isinstance(users, list):
        users = list(users)
    
    user_ids = [u.id if hasattr(u, 'id') else u for u in users]
    print(f"User IDs: {user_ids}")
    logger.info(f"User IDs: {user_ids}")
    
    # Get all active push tokens for these users
    push_tokens = PushToken.objects.filter(
        user_id__in=user_ids,
        is_active=True
    ).values_list('token', flat=True)
    
    token_count = push_tokens.count()
    print(f"Found {token_count} active push tokens")
    logger.info(f"Found {token_count} active push tokens")
    
    if not push_tokens:
        print(f"No active push tokens found for users: {user_ids}")
        logger.warning(f"No active push tokens found for users: {user_ids}")
        return {'status': 'no_tokens', 'count': 0}
    
    # Send to all tokens
    print(f"Calling send_push_notification with {token_count} tokens")
    logger.info(f"Calling send_push_notification with {token_count} tokens")
    result = send_push_notification(
        tokens=list(push_tokens),
        title=title,
        body=description,
        data=data
    )
    
    print(f"send_push_notification result: {result}")
    logger.info(f"send_push_notification result: {result}")
    return result

def create_notification_for_users(*,users,title,description="",full_text="",created_by=None,audience_type=Notification.AUDIENCE_SELECTED_GUIDES,tracking_type=Notification.TRACKING_INFO_ONLY,related_user=None,send_push=True,push_data=None):
    user_ids = list(users.values_list("id", flat=True))
    if not user_ids:
        return None, 0
    notification = Notification.objects.create(title=title,description=description,full_text=full_text or description or title,created_by=created_by,audience_type=audience_type,tracking_type=tracking_type,related_user=related_user)
    UserNotification.objects.bulk_create([UserNotification(user_id=user_id, notification=notification) for user_id in user_ids],ignore_conflicts=True)
    
    # Send push notification if enabled
    if send_push:
        data = {'notification_id': str(notification.id)}
        if push_data:
            data.update(push_data)
        send_push_to_users(
            users=users,
            title=title,
            description=description,
            data=data
        )
    
    return notification, len(user_ids)

def create_notification_for_user(*,user,title,description="",full_text="",created_by=None,related_user=None,send_push=True,push_data=None):
    return create_notification_for_users(
        users=get_user_model().objects.filter(id=user.id),
        title=title,
        description=description,
        full_text=full_text,
        created_by=created_by,
        audience_type=Notification.AUDIENCE_SELECTED_GUIDES,
        tracking_type=Notification.TRACKING_INFO_ONLY,
        related_user=related_user or user,
        send_push=send_push,
        push_data=push_data,
    )

def create_notification_for_staff(*,title,description="",full_text="",created_by=None,related_user=None,send_push=True,push_data=None):
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
        send_push=send_push,
        push_data=push_data,
    )
