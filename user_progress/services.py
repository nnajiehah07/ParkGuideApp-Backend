from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone

from courses.models import ModuleProgress
from notifications.services import create_notification_for_staff, create_notification_for_user

from .models import Badge, UserBadge


def notify_badge_pending_for_admins(user_badge, admin_user=None):
    create_notification_for_staff(
        title=f'Badge approval needed: {user_badge.badge.name}',
        description=f'{user_badge.user.email} is ready for badge review.',
        full_text=(
            f'{user_badge.user.email} has met the requirement for "{user_badge.badge.name}" '
            f'and is now pending approval.'
        ),
        created_by=admin_user,
    )


def notify_badge_granted_to_user(user_badge, admin_user=None):
    create_notification_for_user(
        user=user_badge.user,
        title=f'Badge granted: {user_badge.badge.name}',
        description='A badge has been awarded to your account.',
        full_text=(
            f'Congratulations. Your badge "{user_badge.badge.name}" has been granted'
            f'{f" by {admin_user.email}" if admin_user else ""}.'
        ),
        created_by=admin_user,
    )


def get_user_completed_module_counts(user_ids=None):
    queryset = ModuleProgress.objects.filter(completed=True)
    if user_ids is not None:
        queryset = queryset.filter(user_id__in=user_ids)

    rows = queryset.values('user_id').annotate(completed_modules=Count('id'))
    return {row['user_id']: row['completed_modules'] for row in rows}


def get_user_completed_module_counts_for_badge(badge, user_ids=None):
    queryset = ModuleProgress.objects.filter(completed=True)
    if badge.course_id:
        queryset = queryset.filter(module__course=badge.course)
    if user_ids is not None:
        queryset = queryset.filter(user_id__in=user_ids)

    rows = queryset.values('user_id').annotate(completed_modules=Count('id'))
    return {row['user_id']: row['completed_modules'] for row in rows}


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
        completed_modules = user.moduleprogress_set.filter(
            completed=True,
            module__course=badge.course,
        ).count()
    else:
        completed_modules = user.moduleprogress_set.filter(completed=True).count()
    return completed_modules, badge.required_completed_modules


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
