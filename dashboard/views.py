from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from django.db.models import Count, Q, Avg, Sum
from django.utils import timezone
from datetime import timedelta

from accounts.models import CustomUser
from courses.models import Course, Module, ModuleProgress, CourseProgress
from user_progress.models import Badge, UserBadge
from notifications.models import Notification, UserNotification


def normalize_progress_value(value):
    """Normalize progress values that may be stored as 0..1 ratios or 0..100 percentages."""
    if value is None:
        return 0.0
    progress_value = float(value)
    if 0 <= progress_value <= 1:
        progress_value *= 100
    return max(0.0, min(100.0, progress_value))

def is_staff_or_admin(user):
    """Check if user is staff or admin"""
    return user.is_staff or user.is_superuser

@login_required(login_url='dashboard:login')
def index_redirect(request):
    """Redirect to dashboard or login"""
    if request.user.is_authenticated and is_staff_or_admin(request.user):
        return redirect('dashboard:home')
    return redirect('dashboard:login')

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_home(request):
    """Dashboard overview/home page"""
    context = get_dashboard_stats(request)
    return render(request, 'dashboard/index.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_users(request):
    """User management page"""
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_user':
            username = request.POST.get('username', '').strip()
            email = request.POST.get('email', '').strip().lower()
            password = request.POST.get('password', '')
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            is_staff = request.POST.get('is_staff') == 'on'

            if not username or not email or not password:
                messages.error(request, 'Username, email, and password are required.')
            elif CustomUser.objects.filter(email=email).exists():
                messages.error(request, 'A user with this email already exists.')
            elif CustomUser.objects.filter(username=username).exists():
                messages.error(request, 'A user with this username already exists.')
            else:
                CustomUser.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    is_staff=is_staff,
                )
                messages.success(request, f'User {username} created successfully.')
            return redirect('dashboard:users')

        if action == 'toggle_user_active':
            user_id = request.POST.get('user_id')
            target_user = CustomUser.objects.filter(id=user_id).first()
            if not target_user:
                messages.error(request, 'User not found.')
            elif target_user.id == request.user.id:
                messages.error(request, 'You cannot disable your own account.')
            else:
                target_user.is_active = not target_user.is_active
                target_user.save(update_fields=['is_active'])
                state = 'activated' if target_user.is_active else 'deactivated'
                messages.success(request, f'User {target_user.username} {state}.')
            return redirect('dashboard:users')

        if action == 'toggle_user_staff':
            user_id = request.POST.get('user_id')
            target_user = CustomUser.objects.filter(id=user_id).first()
            if not target_user:
                messages.error(request, 'User not found.')
            elif target_user.id == request.user.id and target_user.is_superuser:
                messages.error(request, 'You cannot remove your own staff access.')
            else:
                target_user.is_staff = not target_user.is_staff
                target_user.save(update_fields=['is_staff'])
                state = 'granted' if target_user.is_staff else 'removed'
                messages.success(request, f'Staff access {state} for {target_user.username}.')
            return redirect('dashboard:users')

    users = CustomUser.objects.all().order_by('-date_joined')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        users = users.filter(
            Q(username__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )
    
    # Pagination
    page = request.GET.get('page', 1)
    per_page = 20
    total_users = users.count()
    start = (int(page) - 1) * per_page
    end = start + per_page
    
    users_paginated = users[start:end]
    total_pages = (total_users + per_page - 1) // per_page
    
    context = {
        'users': users_paginated,
        'total_users': total_users,
        'current_page': int(page),
        'total_pages': total_pages,
        'search_query': search_query,
        'stats': {
            'total_users': CustomUser.objects.count(),
            'active_users': CustomUser.objects.filter(is_active=True).count(),
            'staff_users': CustomUser.objects.filter(is_staff=True).count(),
            'new_this_week': CustomUser.objects.filter(
                date_joined__gte=timezone.now() - timedelta(days=7)
            ).count(),
        }
    }
    return render(request, 'dashboard/users.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_courses(request):
    """Course management page"""
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_course':
            title_en = request.POST.get('title_en', '').strip()
            if not title_en:
                messages.error(request, 'Course title is required.')
            else:
                Course.objects.create(title={'en': title_en})
                messages.success(request, f'Course "{title_en}" created successfully.')
            return redirect('dashboard:courses')

        if action == 'create_module':
            course_id = request.POST.get('course_id')
            module_title_en = request.POST.get('module_title_en', '').strip()
            module_content_en = request.POST.get('module_content_en', '').strip()

            course = Course.objects.filter(id=course_id).first()
            if not course:
                messages.error(request, 'Please select a valid course.')
            elif not module_title_en:
                messages.error(request, 'Module title is required.')
            else:
                Module.objects.create(
                    course=course,
                    title={'en': module_title_en},
                    content={'en': module_content_en} if module_content_en else None,
                )
                messages.success(request, f'Module "{module_title_en}" added to course.')
            return redirect('dashboard:courses')

    courses = Course.objects.annotate(
        total_modules=Count('modules'),
        total_enrollments=Count('courseprogress')
    ).all()
    
    context = {
        'courses': courses,
        'stats': {
            'total_courses': courses.count(),
            'total_modules': Module.objects.count(),
            'avg_modules_per_course': courses.aggregate(avg=Avg('modules'))['avg'] or 0,
        },
        'all_courses': Course.objects.all().order_by('id'),
    }
    return render(request, 'dashboard/courses.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_progress(request):
    """User progress tracking page"""
    # Get course progress with user info
    course_progress = CourseProgress.objects.select_related('user', 'course').all()
    
    # Filter by course if specified
    course_id = request.GET.get('course')
    if course_id:
        course_progress = course_progress.filter(course_id=course_id)
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        course_progress = course_progress.filter(
            Q(user__username__icontains=search_query) |
            Q(user__email__icontains=search_query)
        )
    
    # Sorting
    sort_by = request.GET.get('sort', '-updated_at')
    course_progress = course_progress.order_by(sort_by)
    
    # Pagination
    page = request.GET.get('page', 1)
    per_page = 25
    total = course_progress.count()
    start = (int(page) - 1) * per_page
    end = start + per_page
    
    progress_paginated = course_progress[start:end]
    progress_rows = list(progress_paginated)
    for row in progress_rows:
        row.display_progress = normalize_progress_value(row.progress)

    total_pages = (total + per_page - 1) // per_page

    avg_progress_raw = CourseProgress.objects.aggregate(avg=Avg('progress'))['avg'] or 0
    
    context = {
        'progress': progress_rows,
        'courses': Course.objects.all(),
        'total': total,
        'current_page': int(page),
        'total_pages': total_pages,
        'search_query': search_query,
        'selected_course': course_id,
        'sort_by': sort_by,
        'stats': {
            'avg_progress': normalize_progress_value(avg_progress_raw),
            'completed_courses': CourseProgress.objects.filter(completed=True).count(),
            'in_progress_courses': CourseProgress.objects.filter(completed=False).count(),
        }
    }
    return render(request, 'dashboard/progress.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_badges(request):
    """Badge management and approval page"""
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_badge':
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            course_id = request.POST.get('course_id')
            required_modules = request.POST.get('required_completed_modules') or '1'
            auto_approve = request.POST.get('auto_approve_when_eligible') == 'on'

            if not name:
                messages.error(request, 'Badge name is required.')
                return redirect('dashboard:badges')
            if Badge.objects.filter(name=name).exists():
                messages.error(request, 'A badge with this name already exists.')
                return redirect('dashboard:badges')

            course = None
            if course_id:
                course = Course.objects.filter(id=course_id).first()

            try:
                required_modules_int = max(1, int(required_modules))
            except ValueError:
                required_modules_int = 1

            Badge.objects.create(
                name=name,
                description=description,
                course=course,
                required_completed_modules=required_modules_int,
                auto_approve_when_eligible=auto_approve,
                is_active=True,
            )
            messages.success(request, f'Badge "{name}" created successfully.')
            return redirect('dashboard:badges')

        if action in ('approve_badge', 'reject_badge'):
            user_badge_id = request.POST.get('user_badge_id')
            user_badge = UserBadge.objects.filter(id=user_badge_id).select_related('user', 'badge').first()
            if not user_badge:
                messages.error(request, 'Badge request not found.')
                return redirect('dashboard:badges')

            if action == 'approve_badge':
                user_badge.status = UserBadge.STATUS_GRANTED
                user_badge.is_awarded = True
                user_badge.awarded_by = request.user
                user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by'])
                messages.success(request, f'Approved {user_badge.badge.name} for {user_badge.user.username}.')
            else:
                user_badge.status = UserBadge.STATUS_REJECTED
                user_badge.is_awarded = False
                user_badge.save(update_fields=['status', 'is_awarded'])
                messages.success(request, f'Rejected {user_badge.badge.name} for {user_badge.user.username}.')
            return redirect('dashboard:badges')

    # Get badges
    badges = Badge.objects.annotate(
        total_users=Count('user_badges'),
        pending_approvals=Count('user_badges', filter=Q(user_badges__status='pending')),
        granted_badges=Count('user_badges', filter=Q(user_badges__status='granted')),
    ).all()
    
    # Get pending badge requests
    pending_badges = UserBadge.objects.filter(
        status='pending'
    ).select_related('user', 'badge').order_by('-awarded_at')
    
    # Pagination
    page = request.GET.get('page', 1)
    per_page = 20
    total = pending_badges.count()
    start = (int(page) - 1) * per_page
    end = start + per_page
    
    pending_paginated = pending_badges[start:end]
    total_pages = (total + per_page - 1) // per_page
    
    context = {
        'badges': badges,
        'pending_badges': pending_paginated,
        'total_pending': total,
        'current_page': int(page),
        'total_pages': total_pages,
        'stats': {
            'total_badges': Badge.objects.count(),
            'active_badges': Badge.objects.filter(is_active=True).count(),
            'pending_approvals': UserBadge.objects.filter(status='pending').count(),
            'total_granted': UserBadge.objects.filter(status='granted').count(),
        },
        'courses': Course.objects.all().order_by('id'),
    }
    return render(request, 'dashboard/badges.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_notifications(request):
    """Notification management page"""
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_notification':
            title = request.POST.get('title', '').strip()
            description = request.POST.get('description', '').strip()
            full_text = request.POST.get('full_text', '').strip()
            target_user_id = request.POST.get('target_user', '').strip()

            if not title or not full_text:
                messages.error(request, 'Title and full message are required.')
                return redirect('dashboard:notifications')

            notification = Notification.objects.create(
                title=title,
                description=description,
                full_text=full_text,
                created_by=request.user,
            )

            recipients = CustomUser.objects.filter(is_active=True)
            if target_user_id and target_user_id != 'all':
                recipients = recipients.filter(id=target_user_id)

            recipient_notifications = [
                UserNotification(user=user, notification=notification)
                for user in recipients
            ]

            if recipient_notifications:
                UserNotification.objects.bulk_create(recipient_notifications)

            messages.success(request, f'Notification sent to {len(recipient_notifications)} user(s).')
            return redirect('dashboard:notifications')

    user_notifications = UserNotification.objects.select_related('user', 'notification').all().order_by('-notification__sent_at')
    
    # Filter by read status if specified
    read_status = request.GET.get('status', '')
    if read_status == 'unread':
        user_notifications = user_notifications.filter(is_read=False)
    elif read_status == 'read':
        user_notifications = user_notifications.filter(is_read=True)
    
    # Pagination
    page = request.GET.get('page', 1)
    per_page = 30
    total = user_notifications.count()
    start = (int(page) - 1) * per_page
    end = start + per_page
    
    notif_paginated = user_notifications[start:end]
    total_pages = (total + per_page - 1) // per_page
    
    context = {
        'notifications': notif_paginated,
        'total': total,
        'current_page': int(page),
        'total_pages': total_pages,
        'selected_status': read_status,
        'users': CustomUser.objects.filter(is_active=True).order_by('username'),
        'stats': {
            'total_notifications': Notification.objects.count(),
            'unread': UserNotification.objects.filter(is_read=False).count(),
            'sent_this_week': Notification.objects.filter(
                sent_at__gte=timezone.now() - timedelta(days=7)
            ).count(),
        }
    }
    return render(request, 'dashboard/notifications.html', context)

def get_dashboard_stats(request):
    """Get overall dashboard statistics"""
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    
    avg_progress_raw = CourseProgress.objects.aggregate(avg=Avg('progress'))['avg'] or 0

    return {
        'stats': {
            'total_users': CustomUser.objects.count(),
            'active_users': CustomUser.objects.filter(is_active=True).count(),
            'new_this_week': CustomUser.objects.filter(date_joined__gte=week_ago).count(),
            'staff_users': CustomUser.objects.filter(is_staff=True).count(),
        },
        'course_stats': {
            'total_courses': Course.objects.count(),
            'total_modules': Module.objects.count(),
            'avg_progress': normalize_progress_value(avg_progress_raw),
        },
        'badge_stats': {
            'total_badges': Badge.objects.count(),
            'pending_approvals': UserBadge.objects.filter(status='pending').count(),
            'granted_this_month': UserBadge.objects.filter(
                awarded_at__gte=month_ago,
                status='granted'
            ).count(),
        },
        'notification_stats': {
            'total_sent': Notification.objects.count(),
            'unread': UserNotification.objects.filter(is_read=False).count(),
            'sent_this_week': Notification.objects.filter(sent_at__gte=week_ago).count(),
        },
        'recent_activity': get_recent_activity(),
    }

def get_recent_activity():
    """Get recent activity for dashboard"""
    activities = []
    
    # Recent new users
    new_users = CustomUser.objects.all().order_by('-date_joined')[:5]
    for user in new_users:
        activities.append({
            'type': 'user_signup',
            'user': user,
            'timestamp': user.date_joined,
            'description': f'New user {user.username} joined'
        })
    
    # Recent badges granted
    recent_badges = UserBadge.objects.filter(
        status='granted'
    ).select_related('user', 'badge').order_by('-awarded_at')[:5]
    for badge in recent_badges:
        activities.append({
            'type': 'badge_granted',
            'user': badge.user,
            'badge': badge.badge,
            'timestamp': badge.awarded_at,
            'description': f'{badge.user.username} earned {badge.badge.name}'
        })
    
    # Recent course completions
    completed = CourseProgress.objects.filter(
        completed=True
    ).select_related('user', 'course').order_by('-updated_at')[:5]
    for progress in completed:
        activities.append({
            'type': 'course_completed',
            'user': progress.user,
            'course': progress.course,
            'timestamp': progress.updated_at,
            'description': f'{progress.user.username} completed course'
        })
    
    # Sort by timestamp
    activities.sort(key=lambda x: x['timestamp'], reverse=True)
    return activities[:10]
