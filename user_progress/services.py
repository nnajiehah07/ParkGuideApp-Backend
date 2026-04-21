from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone
from courses.models import ModuleProgress, ChapterProgress
from django.conf import settings
from notifications.services import create_notification_for_staff, create_notification_for_user
from secure_files.services.firebase_storage import generate_download_url
from .models import Badge, UserBadge

DEFAULT_BADGE_STORAGE_PATH = 'assests/badges'
DEFAULT_BADGE_FILENAMES = {
    'park-guide-101': 'park-guide-101.jpg',
    'park-guide-201': 'park-guide-201.jpg',
    'park-guide-301': 'park-guide-301.jpg',
    'park-guide-401': 'park-guide-401.png',
    'park-guide-501': 'park-guide-501.jpg',
}


def get_course_badge_requirement_count(course):
    """Use the fuller of legacy modules or chapter-based content as the completion requirement."""
    module_count = course.modules.count() if hasattr(course, 'modules') else 0
    chapter_count = course.chapters.count() if hasattr(course, 'chapters') else 0
    return max(module_count, chapter_count, 1)


def get_default_badge_blob_path(course):
    filename = DEFAULT_BADGE_FILENAMES.get(course.code)
    if not filename:
        return ''
    return f'{DEFAULT_BADGE_STORAGE_PATH}/{filename}'


def build_firebase_media_url(blob_path):
    if not blob_path:
        return ''
    bucket_name = getattr(settings, 'FIREBASE_STORAGE_BUCKET', '').strip()
    if not bucket_name:
        return ''
    encoded_path = blob_path.replace('/', '%2F')
    return f'https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{encoded_path}?alt=media'


def get_badge_storage_path(value):
    if not value:
        return ''

    value = value.strip()
    bucket_name = getattr(settings, 'FIREBASE_STORAGE_BUCKET', '').strip()

    if value.startswith('gs://'):
        path = value.split('/', 3)
        return path[3] if len(path) > 3 else ''

    firebase_prefix = f'https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/'
    if bucket_name and value.startswith(firebase_prefix):
        encoded_part = value[len(firebase_prefix):].split('?', 1)[0]
        return encoded_part.replace('%2F', '/')

    if value.startswith(f'{DEFAULT_BADGE_STORAGE_PATH}/'):
        return value

    return ''


def get_badge_image_access_url(raw_value):
    storage_path = get_badge_storage_path(raw_value)
    if storage_path:
        try:
            return generate_download_url(storage_path)
        except Exception:
            return raw_value
    return raw_value


def build_course_badge_metadata(course):
    course_title = course.title.get('en', f'Course {course.id}')
    course_description = (course.description or {}).get('en', '').strip()
    skills_awarded = list(
        course.chapters.order_by('order').values_list('title__en', flat=True)
    )
    lesson_highlights = list(
        course.chapters.order_by('order', 'lessons__order').values_list('lessons__title__en', flat=True)
    )

    summary_bits = []
    if skills_awarded:
        summary_bits.append(f"Skills covered: {', '.join(skills_awarded[:4])}")
    if lesson_highlights:
        summary_bits.append(f"Lessons completed: {', '.join(lesson_highlights[:4])}")

    default_blob_path = get_default_badge_blob_path(course)
    default_image_url = build_firebase_media_url(default_blob_path)

    return {
        'name': f'{course_title} Completion Badge',
        'description': course_description or f'Awarded for completing the {course_title} course.',
        'badge_image_url': default_blob_path or course.thumbnail or '',
        'badge_image_source': default_blob_path or course.thumbnail or '',
        'skills_awarded': skills_awarded,
        'lesson_highlights': lesson_highlights,
        'required_completed_modules': get_course_badge_requirement_count(course),
        'required_badges_count': 0,
        'course': course,
        'is_major_badge': False,
        'is_active': True,
        'auto_approve_when_eligible': False,
    }


def create_or_update_course_badge(course):
    badge_defaults = build_course_badge_metadata(course)
    badge = Badge.objects.filter(course=course, is_major_badge=False).order_by('id').first()
    if badge:
        badge.name = badge_defaults['name']
        badge.course = course
        badge.required_completed_modules = badge_defaults['required_completed_modules']
        badge.required_badges_count = 0
        badge.is_major_badge = False
        badge.is_active = True
        badge.auto_approve_when_eligible = False

        if not (badge.description or '').strip():
            badge.description = badge_defaults['description']
        if not (badge.badge_image_url or '').strip():
            badge.badge_image_url = badge_defaults['badge_image_url']
        if not (badge.badge_image_source or '').strip():
            badge.badge_image_source = badge_defaults['badge_image_source']
        if not badge.skills_awarded:
            badge.skills_awarded = badge_defaults['skills_awarded']
        if not badge.lesson_highlights:
            badge.lesson_highlights = badge_defaults['lesson_highlights']

        badge.save()
        return badge

    badge = Badge.objects.create(**badge_defaults)
    return badge


def notify_badge_pending_for_admins(user_badge, admin_user=None):
    create_notification_for_staff(
        title=f'Badge approval needed: {user_badge.badge.name}',
        description=f'{user_badge.user.email} completed the required course and is ready for badge review.',
        full_text=(
            f'{user_badge.user.email} has completed the requirement for "{user_badge.badge.name}" '
            f'and the badge is now pending admin approval.'
        ),
        created_by=admin_user,
        related_user=user_badge.user,
    )


def notify_badge_granted_to_user(user_badge, admin_user=None):
    create_notification_for_user(
        user=user_badge.user,
        title=f'Badge granted: {user_badge.badge.name}',
        description='Your completed course badge has been approved.',
        full_text=(
            f'Congratulations.\nYour badge "{user_badge.badge.name}" has been approved and granted'
            f'{f" by {admin_user.email}" if admin_user else ""}.'
        ),
        created_by=admin_user,
        related_user=user_badge.user,
    )


def get_user_completed_module_counts(user_ids=None):
    module_queryset = ModuleProgress.objects.filter(completed=True)
    chapter_queryset = ChapterProgress.objects.filter(is_complete=True)
    if user_ids is not None:
        module_queryset = module_queryset.filter(user_id__in=user_ids)
        chapter_queryset = chapter_queryset.filter(user_id__in=user_ids)

    module_rows = module_queryset.values('user_id').annotate(completed_modules=Count('id'))
    chapter_rows = chapter_queryset.values('user_id').annotate(completed_chapters=Count('id'))

    module_counts = {row['user_id']: row['completed_modules'] for row in module_rows}
    chapter_counts = {row['user_id']: row['completed_chapters'] for row in chapter_rows}

    merged = {}
    all_user_ids = set(module_counts.keys()) | set(chapter_counts.keys())
    for user_id in all_user_ids:
        merged[user_id] = max(module_counts.get(user_id, 0), chapter_counts.get(user_id, 0))
    return merged


def get_user_completed_module_counts_for_badge(badge, user_ids=None):
    module_queryset = ModuleProgress.objects.filter(completed=True)
    chapter_queryset = ChapterProgress.objects.filter(is_complete=True)
    if badge.course_id:
        module_queryset = module_queryset.filter(module__course=badge.course)
        chapter_queryset = chapter_queryset.filter(chapter__course=badge.course)
    if user_ids is not None:
        module_queryset = module_queryset.filter(user_id__in=user_ids)
        chapter_queryset = chapter_queryset.filter(user_id__in=user_ids)

    module_rows = module_queryset.values('user_id').annotate(completed_modules=Count('id'))
    chapter_rows = chapter_queryset.values('user_id').annotate(completed_chapters=Count('id'))

    module_counts = {row['user_id']: row['completed_modules'] for row in module_rows}
    chapter_counts = {row['user_id']: row['completed_chapters'] for row in chapter_rows}

    merged = {}
    all_user_ids = set(module_counts.keys()) | set(chapter_counts.keys())
    for user_id in all_user_ids:
        merged[user_id] = max(module_counts.get(user_id, 0), chapter_counts.get(user_id, 0))
    return merged


def get_user_granted_regular_badge_counts(user_ids=None):
    queryset = UserBadge.objects.filter(
        status=UserBadge.STATUS_GRANTED,
        badge__is_major_badge=False,
    )
    if user_ids is not None:
        queryset = queryset.filter(user_id__in=user_ids)
    rows = queryset.values('user_id').annotate(granted_badges=Count('id'))
    return {row['user_id']: row['granted_badges'] for row in rows}


def get_user_requirement_progress_for_badge(badge, user):
    if badge.is_major_badge:
        granted_badges_count = user.badge_progress.filter(
            status=UserBadge.STATUS_GRANTED,
            badge__is_major_badge=False,
        ).count()
        return granted_badges_count, badge.required_badges_count

    if badge.course_id:
        completed_modules_legacy = user.moduleprogress_set.filter(
            completed=True,
            module__course=badge.course,
        ).count()
        completed_chapters = user.chapter_progress.filter(
            is_complete=True,
            chapter__course=badge.course,
        ).count()
    else:
        completed_modules_legacy = user.moduleprogress_set.filter(completed=True).count()
        completed_chapters = user.chapter_progress.filter(is_complete=True).count()

    completed_units = max(completed_modules_legacy, completed_chapters)
    return completed_units, badge.required_completed_modules


def ensure_badge_rows_for_user(user):
    badges = Badge.objects.filter(is_active=True)
    created_count = 0
    for badge in badges:
        _, created = UserBadge.objects.get_or_create(
            user=user,
            badge=badge,
            defaults={
                'status': UserBadge.STATUS_IN_PROGRESS,
                'is_awarded': False,
            },
        )
        if created:
            created_count += 1
    return created_count


def ensure_badge_rows_for_all_users():
    users = get_user_model().objects.all()
    created_count = 0
    for user in users:
        created_count += ensure_badge_rows_for_user(user)
    return created_count


# ============================================================================
# NEW BADGE SYSTEM FUNCTIONS - Auto-grant on course completion
# ============================================================================

def grant_course_completion_badge(user, course):
    """
    Grant course completion badge to user when they complete a course.
    
    Args:
        user: The user who completed the course
        course: The course that was completed
        
    Returns:
        True if badge was newly granted, False otherwise
    """
    from django.db.models import Q
    
    badge = create_or_update_course_badge(course)
    
    # Check if user already has this badge in granted status
    user_badge_qs = UserBadge.objects.filter(user=user, badge=badge)
    if user_badge_qs.filter(status='granted', is_awarded=True).exists():
        return False  # Already has this badge
    
    # Course badges always go through admin review after completion.
    user_badge, created = UserBadge.objects.get_or_create(
        user=user,
        badge=badge,
        defaults={
            'status': UserBadge.STATUS_PENDING,
            'is_awarded': False,
            'awarded_at': timezone.now(),
        }
    )
    
    if created:
        notify_badge_pending_for_admins(user_badge, admin_user=None)
        check_and_grant_achievement_badges(user)
        return True
    
    # Badge already exists but in different status
    if user_badge.status in ['pending', 'in_progress', 'rejected']:
        user_badge.status = UserBadge.STATUS_PENDING
        user_badge.is_awarded = False
        user_badge.awarded_at = timezone.now()
        user_badge.save()
        notify_badge_pending_for_admins(user_badge, admin_user=None)
        return True
    
    return False


def check_and_grant_achievement_badges(user):
    """
    Check if user is eligible for any achievement badges and grant them automatically.
    
    Achievement badges (major badges) are auto-approved when:
    - User has reached the required milestone (e.g., 3 courses completed)
    - Badge has auto_approve_when_eligible=True
    
    Args:
        user: The user to check for achievement badges
    """
    from django.db.models import Q, Count
    
    # Count user's granted course completion badges
    granted_course_badges = UserBadge.objects.filter(
        user=user,
        badge__is_major_badge=False,
        badge__course__isnull=False,
        status='granted',
        is_awarded=True
    ).count()
    
    # Get all achievement badges and check eligibility
    achievement_badges = Badge.objects.filter(
        is_major_badge=True,
        is_active=True
    ).order_by('required_badges_count')
    
    for badge in achievement_badges:
        if granted_course_badges >= badge.required_badges_count:
            # User is eligible for this achievement badge
            user_badge_qs = UserBadge.objects.filter(user=user, badge=badge)
            
            if not user_badge_qs.filter(status='granted', is_awarded=True).exists():
                user_badge, created = UserBadge.objects.get_or_create(
                    user=user,
                    badge=badge,
                    defaults={
                        'status': UserBadge.STATUS_IN_PROGRESS,
                        'is_awarded': False,
                        'awarded_at': timezone.now(),
                    }
                )
                
                # Check if badge should be auto-approved
                if badge.auto_approve_when_eligible:
                    # AUTO-GRANT: No admin approval needed
                    user_badge.status = UserBadge.STATUS_GRANTED
                    user_badge.is_awarded = True
                    user_badge.awarded_at = timezone.now()
                    user_badge.awarded_by = None  # System-awarded
                    user_badge.save()
                    
                    # Notify user of automatic achievement
                    notify_badge_granted_to_user(user_badge, admin_user=None)
                else:
                    # Requires admin approval - mark as PENDING
                    user_badge.status = UserBadge.STATUS_PENDING
                    user_badge.is_awarded = False
                    user_badge.awarded_at = timezone.now()
                    user_badge.save()
                    
                    # Notify admins for approval
                    notify_badge_pending_for_admins(user_badge, admin_user=None)
                    
            elif user_badge.status in ['pending', 'in_progress', 'rejected']:
                # User was previously rejected or pending - recheck eligibility
                if badge.auto_approve_when_eligible:
                    # Auto-grant if eligible and badge allows auto-approval
                    user_badge.status = UserBadge.STATUS_GRANTED
                    user_badge.is_awarded = True
                    user_badge.awarded_at = timezone.now()
                    user_badge.awarded_by = None
                    user_badge.save()
                    notify_badge_granted_to_user(user_badge, admin_user=None)
                else:
                    # Resubmit for admin approval
                    user_badge.status = UserBadge.STATUS_PENDING
                    user_badge.is_awarded = False
                    user_badge.awarded_at = timezone.now()
                    user_badge.save()
                    notify_badge_pending_for_admins(user_badge, admin_user=None)


def revoke_badge(user, badge, admin_user=None, reason=''):
    """
    Revoke a badge from a user.
    
    Args:
        user: The user to revoke the badge from
        badge: The badge to revoke
        admin_user: The admin revoking the badge
        reason: Reason for revocation (optional)
        
    Returns:
        True if revocation was successful, False otherwise
    """
    try:
        user_badge = UserBadge.objects.get(user=user, badge=badge)
        user_badge.status = 'rejected'
        user_badge.revoked_at = timezone.now()
        user_badge.revoked_by = admin_user
        user_badge.is_awarded = False
        user_badge.save()
        
        notify_badge_revoked_to_user(user_badge, admin_user)
        return True
    except UserBadge.DoesNotExist:
        return False


def re_grant_badge(user, badge, admin_user=None):
    """
    Re-grant a previously revoked badge to a user.
    
    Args:
        user: The user to grant the badge to
        badge: The badge to grant
        admin_user: The admin granting the badge
        
    Returns:
        True if successful, False otherwise
    """
    try:
        user_badge = UserBadge.objects.get(user=user, badge=badge)
        user_badge.status = 'granted'
        user_badge.is_awarded = True
        user_badge.awarded_at = timezone.now()
        user_badge.awarded_by = admin_user
        user_badge.revoked_at = None
        user_badge.revoked_by = None
        user_badge.save()
        
        notify_badge_granted_to_user(user_badge, admin_user)
        return True
    except UserBadge.DoesNotExist:
        return False


def get_user_badge_stats(user):
    """
    Get badge statistics for a user.
    
    Args:
        user: The user to get stats for
        
    Returns:
        Dictionary with badge statistics
    """
    all_badges = UserBadge.objects.filter(user=user)
    granted = all_badges.filter(status='granted', is_awarded=True).count()
    pending = all_badges.filter(status='pending').count()
    in_progress = all_badges.filter(status='in_progress').count()
    rejected = all_badges.filter(status='rejected').count()
    revoked = all_badges.filter(revoked_at__isnull=False).count()
    
    # Get course completion count
    course_completions = UserBadge.objects.filter(
        user=user,
        badge__is_major_badge=False,
        badge__course__isnull=False,
        status='granted',
        is_awarded=True
    ).count()
    
    # Get achievement badges
    achievement_badges = UserBadge.objects.filter(
        user=user,
        badge__is_major_badge=True,
        status='granted',
        is_awarded=True
    ).count()
    
    return {
        'total': all_badges.count(),
        'granted': granted,
        'pending': pending,
        'in_progress': in_progress,
        'rejected': rejected,
        'revoked': revoked,
        'course_completions': course_completions,
        'achievement_badges': achievement_badges,
    }


def get_badge_leaderboard(limit=10):
    """
    Get top users by number of granted badges.
    
    Args:
        limit: Number of top users to return
        
    Returns:
        QuerySet of users with badge counts
    """
    from django.db.models import Q, Count
    
    User = get_user_model()
    
    leaderboard = User.objects.annotate(
        badge_count=Count('badge_progress', filter=Q(
            badge_progress__status='granted',
            badge_progress__is_awarded=True
        )),
        course_badges=Count('badge_progress', filter=Q(
            badge_progress__status='granted',
            badge_progress__is_awarded=True,
            badge_progress__badge__is_major_badge=False
        )),
        achievement_badges=Count('badge_progress', filter=Q(
            badge_progress__status='granted',
            badge_progress__is_awarded=True,
            badge_progress__badge__is_major_badge=True
        ))
    ).filter(
        badge_count__gt=0
    ).order_by('-badge_count')[:limit]
    
    return leaderboard


def notify_badge_revoked_to_user(user_badge, admin_user=None):
    """Send notification to user when badge is revoked."""
    create_notification_for_user(
        user=user_badge.user,
        title=f'Badge revoked: {user_badge.badge.name}',
        description='A badge has been revoked from your account.',
        full_text=(
            f'Your badge "{user_badge.badge.name}" has been revoked'
            f'{f" by {admin_user.email}" if admin_user else ""}.'
        ),
        created_by=admin_user,
        related_user=user_badge.user,
    )


def evaluate_user_badge(user, badge, admin_user=None, completed_count=None, granted_badges_count=None):
    user_badge, created = UserBadge.objects.get_or_create(
        user=user,
        badge=badge,
        defaults={
            'status': UserBadge.STATUS_IN_PROGRESS,
            'is_awarded': False,
        },
    )

    if not badge.is_active:
        return user_badge, created, False

    if badge.is_major_badge:
        progress_value = granted_badges_count if granted_badges_count is not None else user.badge_progress.filter(
            status=UserBadge.STATUS_GRANTED,
            badge__is_major_badge=False,
        ).count()
        eligible = progress_value >= badge.required_badges_count
    else:
        progress_value = completed_count if completed_count is not None else get_user_requirement_progress_for_badge(badge, user)[0]
        eligible = progress_value >= badge.required_completed_modules

    changed = False
    if eligible:
        if user_badge.status == UserBadge.STATUS_GRANTED:
            target_status = UserBadge.STATUS_GRANTED
        else:
            target_status = UserBadge.STATUS_GRANTED if badge.is_major_badge or badge.auto_approve_when_eligible else UserBadge.STATUS_PENDING
        target_awarded = target_status == UserBadge.STATUS_GRANTED
        target_awarded_by = admin_user if (target_status == UserBadge.STATUS_GRANTED and admin_user is not None) else user_badge.awarded_by

        if user_badge.status != target_status or user_badge.is_awarded != target_awarded or user_badge.revoked_at is not None or user_badge.revoked_by is not None:
            previous_status = user_badge.status
            user_badge.status = target_status
            user_badge.is_awarded = target_awarded
            user_badge.awarded_by = target_awarded_by
            user_badge.revoked_at = None
            user_badge.revoked_by = None
            user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by', 'revoked_at', 'revoked_by'])
            if target_status == UserBadge.STATUS_PENDING and previous_status != UserBadge.STATUS_PENDING:
                notify_badge_pending_for_admins(user_badge, admin_user=admin_user)
            if target_status == UserBadge.STATUS_GRANTED and previous_status != UserBadge.STATUS_GRANTED:
                notify_badge_granted_to_user(user_badge, admin_user=admin_user)
            changed = True
        return user_badge, created, changed

    if user_badge.status in (UserBadge.STATUS_PENDING, UserBadge.STATUS_GRANTED, UserBadge.STATUS_IN_PROGRESS):
        if user_badge.status != UserBadge.STATUS_IN_PROGRESS or user_badge.is_awarded:
            user_badge.status = UserBadge.STATUS_IN_PROGRESS
            user_badge.is_awarded = False
            user_badge.awarded_by = None
            user_badge.revoked_at = None
            user_badge.revoked_by = None
            user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by', 'revoked_at', 'revoked_by'])
            changed = True

    return user_badge, created, changed


def sync_user_badges(user, admin_user=None):
    ensure_badge_rows_for_user(user)
    badges = list(Badge.objects.filter(is_active=True).select_related('course').order_by('is_major_badge', 'id'))
    if not badges:
        return {'created': 0, 'in_progress': 0, 'pending': 0, 'granted': 0}

    non_major_badges = [badge for badge in badges if not badge.is_major_badge]
    major_badges = [badge for badge in badges if badge.is_major_badge]

    created_total = 0
    in_progress_total = 0
    pending_total = 0
    granted_total = 0

    completed_counts_by_badge = {
        badge.id: get_user_requirement_progress_for_badge(badge, user)[0]
        for badge in non_major_badges
    }

    for badge in non_major_badges:
        user_badge, created, changed = evaluate_user_badge(
            user,
            badge,
            admin_user=admin_user,
            completed_count=completed_counts_by_badge.get(badge.id, 0),
        )
        if created:
            created_total += 1
        if changed:
            if user_badge.status == UserBadge.STATUS_IN_PROGRESS:
                in_progress_total += 1
            elif user_badge.status == UserBadge.STATUS_PENDING:
                pending_total += 1
            elif user_badge.status == UserBadge.STATUS_GRANTED:
                granted_total += 1

    granted_regular_badges_count = user.badge_progress.filter(
        status=UserBadge.STATUS_GRANTED,
        badge__is_major_badge=False,
    ).count()

    for badge in major_badges:
        user_badge, created, changed = evaluate_user_badge(
            user,
            badge,
            admin_user=admin_user,
            granted_badges_count=granted_regular_badges_count,
        )
        if created:
            created_total += 1
        if changed:
            if user_badge.status == UserBadge.STATUS_IN_PROGRESS:
                in_progress_total += 1
            elif user_badge.status == UserBadge.STATUS_PENDING:
                pending_total += 1
            elif user_badge.status == UserBadge.STATUS_GRANTED:
                granted_total += 1

    return {
        'created': created_total,
        'in_progress': in_progress_total,
        'pending': pending_total,
        'granted': granted_total,
    }


def sync_all_badges_for_all_users(admin_user=None):
    summary = {'created': 0, 'in_progress': 0, 'pending': 0, 'granted': 0}
    User = get_user_model()
    for user in User.objects.all():
        user_summary = sync_user_badges(user, admin_user=admin_user)
        for key in summary:
            summary[key] += user_summary[key]
    return summary


def sync_pending_badges_for_eligible_users(badge, admin_user=None):
    if not badge.is_active:
        return 0, 0, 0

    created_pending_count = 0
    moved_to_pending_count = 0
    auto_granted_count = 0
    User = get_user_model()

    for user in User.objects.all():
        before = UserBadge.objects.filter(user=user, badge=badge).first()
        before_status = before.status if before else None
        user_badge, created, changed = evaluate_user_badge(user, badge, admin_user=admin_user)

        if not created and not changed:
            continue
        if user_badge.status == UserBadge.STATUS_PENDING:
            if created or before_status is None:
                created_pending_count += 1
            elif before_status != UserBadge.STATUS_PENDING:
                moved_to_pending_count += 1
        elif user_badge.status == UserBadge.STATUS_GRANTED:
            auto_granted_count += 1

    return created_pending_count, moved_to_pending_count, auto_granted_count


def auto_approve_pending_badges(badge, admin_user=None):
    pending_badges = UserBadge.objects.filter(badge=badge, status=UserBadge.STATUS_PENDING).select_related('user')
    if not pending_badges.exists():
        return 0

    approved_count = 0
    for user_badge in pending_badges:
        user_badge.status = UserBadge.STATUS_GRANTED
        user_badge.is_awarded = True
        user_badge.awarded_by = admin_user
        user_badge.revoked_at = None
        user_badge.revoked_by = None
        user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by', 'revoked_at', 'revoked_by'])
        notify_badge_granted_to_user(user_badge, admin_user=admin_user)
        approved_count += 1

    sync_all_major_badges_for_all_users(admin_user=admin_user)
    return approved_count


def auto_reject_pending_badges(badge, admin_user=None):
    pending_badges = UserBadge.objects.filter(badge=badge, status=UserBadge.STATUS_PENDING)
    if not pending_badges.exists():
        return 0

    now = timezone.now()
    rejected_count = 0

    for user_badge in pending_badges:
        user_badge.status = UserBadge.STATUS_REJECTED
        user_badge.is_awarded = False
        user_badge.revoked_at = now
        user_badge.revoked_by = admin_user
        user_badge.save(update_fields=['status', 'is_awarded', 'revoked_at', 'revoked_by'])
        rejected_count += 1

    return rejected_count


def revoke_badge_from_ineligible_users(badge, admin_user=None):
    active_badges = UserBadge.objects.filter(badge=badge, status=UserBadge.STATUS_GRANTED).select_related('user')
    if not active_badges.exists():
        return 0

    revoked_count = 0

    for user_badge in active_badges:
        evaluate_user_badge(user_badge.user, badge, admin_user=admin_user)
        user_badge.refresh_from_db()
        if user_badge.status == UserBadge.STATUS_IN_PROGRESS:
            revoked_count += 1

    return revoked_count


def sync_all_major_badges_for_all_users(admin_user=None):
    major_badges = Badge.objects.filter(is_active=True, is_major_badge=True)
    if not major_badges.exists():
        return 0

    synced_total = 0
    User = get_user_model()
    for user in User.objects.all():
        granted_regular_badges_count = user.badge_progress.filter(
            status=UserBadge.STATUS_GRANTED,
            badge__is_major_badge=False,
        ).count()
        for badge in major_badges:
            _, _, changed = evaluate_user_badge(
                user,
                badge,
                admin_user=admin_user,
                granted_badges_count=granted_regular_badges_count,
            )
            if changed:
                synced_total += 1
    return synced_total
