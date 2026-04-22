from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Q, Avg, Sum
from django.utils import timezone
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import send_mail
from django.conf import settings
from .forms import CourseForm, CourseImportForm, ChapterForm, LessonForm, QuizForm, PracticeExerciseForm
from django.core.management import call_command
from datetime import timedelta
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.http import HttpResponseRedirect
from django.utils.timesince import timesince
from io import StringIO, BytesIO
import tempfile
import os
import json
from firebase_admin import storage
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from datetime import datetime
import secrets
import string

from accounts.models import CustomUser, AccountApplication
from accounts.services import delete_application_cv, generate_application_cv_url
from courses.models import (
    Course, Module, ModuleProgress, CourseProgress, Chapter, Lesson, Quiz, 
    ChapterProgress, CourseEnrollment, QuizAttempt, PracticeAttempt, LessonProgress
)
from user_progress.models import Badge, UserBadge
from notifications.models import Notification, UserNotification
from notifications.services import send_push_to_users
from secure_files.models import SecureFile
from secure_files.services.firebase_storage import delete_file as delete_secure_blob, upload_file
from .models import BackupSetting, BackupHistory, BackupAuditLog


def get_title_text(title_obj, lang='en', default='Untitled'):
    """Safely extract title text from either dict or string format"""
    if isinstance(title_obj, dict):
        return title_obj.get(lang, title_obj.get('en', default))
    elif isinstance(title_obj, str):
        return title_obj or default
    return default


@require_http_methods(["GET"])
def dashboard_sso_login(request):
    """Create a Django session from a valid admin JWT token for the web dashboard."""
    token = request.GET.get('token', '').strip()
    if not token:
        return redirect('dashboard:login')

    try:
        access_token = AccessToken(token)
        user_id = access_token.get('user_id')
        user = CustomUser.objects.get(id=user_id)
    except (TokenError, CustomUser.DoesNotExist, TypeError, ValueError):
        return redirect('dashboard:login')

    if not (user.is_staff or user.is_superuser or getattr(user, 'user_type', '') == 'admin'):
        return redirect('dashboard:login')

    auth_login(request, user)
    return redirect('dashboard:home')


def build_backup_json():
    """Build a JSON dump of database content for backup/export."""
    buffer = StringIO()
    call_command(
        'dumpdata',
        natural_foreign=True,
        natural_primary=True,
        indent=2,
        stdout=buffer,
    )
    return buffer.getvalue()


def get_or_create_backup_setting():
    setting, _ = BackupSetting.objects.get_or_create(pk=1)
    return setting


def compute_next_backup_time(base_time, frequency):
    if frequency == BackupSetting.FREQUENCY_HOURLY:
        return base_time + timedelta(hours=1)
    if frequency == BackupSetting.FREQUENCY_WEEKLY:
        return base_time + timedelta(days=7)
    return base_time + timedelta(days=1)


def upload_backup_json_to_firebase(content, prefix='system_backups'):
    now = timezone.now()
    safe_prefix = (prefix or 'system_backups').strip('/')
    filename = f'backup_{now.strftime("%Y%m%d_%H%M%S")}.json'
    blob_path = f'{safe_prefix}/{filename}'
    bucket = storage.bucket()
    blob = bucket.blob(blob_path)
    blob.upload_from_string(content, content_type='application/json')
    return blob_path


def validate_backup_json_content(content):
    """Validate backup JSON structure and return summary information."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return False, f'Invalid JSON: {exc}', {}

    if not isinstance(payload, list):
        return False, 'Backup JSON must be a list of objects.', {}

    model_counts = {}
    for item in payload:
        if not isinstance(item, dict):
            return False, 'Backup contains invalid entries (expected objects).', {}
        model_name = item.get('model')
        if not model_name:
            return False, 'Backup entries missing model field.', {}
        model_counts[model_name] = model_counts.get(model_name, 0) + 1

    summary = {
        'total_records': len(payload),
        'total_models': len(model_counts),
        'model_counts': model_counts,
    }
    return True, '', summary


def summarize_backup_json_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as backup_file:
        content = backup_file.read()
    return validate_backup_json_content(content)


def log_backup_history(*, request_user, action_type, status, destination='', blob_path='', file_size_bytes=0, integrity_ok=False, details=''):
    BackupHistory.objects.create(
        triggered_by=request_user,
        action_type=action_type,
        status=status,
        destination=destination,
        blob_path=blob_path,
        file_size_bytes=file_size_bytes,
        integrity_ok=integrity_ok,
        details=details,
    )


def log_backup_audit(*, request_user, action, metadata=''):
    BackupAuditLog.objects.create(
        user=request_user,
        action=action,
        metadata=metadata,
    )


def apply_firebase_backup_retention(prefix, keep_count):
    """Delete old backup files beyond retention count for given prefix."""
    if keep_count <= 0:
        return []

    safe_prefix = (prefix or 'system_backups').strip('/') + '/'
    bucket = storage.bucket()
    blobs = [blob for blob in bucket.list_blobs(prefix=safe_prefix) if blob.name.endswith('.json')]
    blobs.sort(key=lambda blob: blob.updated or timezone.now(), reverse=True)

    deleted = []
    for blob in blobs[keep_count:]:
        deleted.append(blob.name)
        blob.delete()

    return deleted


def generate_firebase_coverage_report():
    """Compare secure file paths in DB against objects present in Firebase."""
    keys = list(SecureFile.objects.values_list('s3_key', flat=True))
    if not keys:
        return {
            'total_db_files': 0,
            'matched_files': 0,
            'missing_files': 0,
            'missing_examples': [],
        }

    bucket = storage.bucket()
    existing = set()
    for key in keys:
        blob = bucket.blob(key)
        if blob.exists():
            existing.add(key)

    missing = [key for key in keys if key not in existing]
    return {
        'total_db_files': len(keys),
        'matched_files': len(existing),
        'missing_files': len(missing),
        'missing_examples': missing[:20],
    }


def pretty_bytes(num):
    size = float(num)
    units = ['B', 'KB', 'MB', 'GB']
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f'{size:.2f} {unit}'
        size /= 1024

def normalize_progress_value(value):
    """Normalize progress values that may be stored as 0..1 ratios or 0..100 percentages."""
    if value is None:
        return 0.0
    progress_value = float(value)
    if 0 <= progress_value <= 1:
        progress_value *= 100
    return max(0.0, min(100.0, progress_value))


def get_display_title(value, fallback='Untitled'):
    """Return a readable label from multilingual JSON title fields."""
    if isinstance(value, dict):
        for key in ('en', 'title', 'name'):
            text = value.get(key)
            if text:
                return str(text)
        for text in value.values():
            if text:
                return str(text)
    if value:
        return str(value)
    return fallback


def build_learning_insight_data(selected_course_id=None):
    """Build filterable chart data for course learning insights."""
    selected_course_id = str(selected_course_id or 'all')
    all_courses = list(Course.objects.prefetch_related('modules').all())
    all_progress = CourseProgress.objects.all()
    guide_qs = get_guide_queryset()
    total_guides = guide_qs.count()
    total_modules = Module.objects.count()
    if total_guides > 0:
        modules_completed_by_all_guides = (
            Module.objects.annotate(
                completed_guides=Count(
                    'moduleprogress__user',
                    filter=Q(
                        moduleprogress__completed=True,
                        moduleprogress__user__in=guide_qs,
                    ),
                    distinct=True,
                )
            )
            .filter(completed_guides=total_guides)
            .count()
        )
    else:
        modules_completed_by_all_guides = 0
    datasets = {
        'all': {
            'label': 'All Courses',
            'learner_status': {
                'labels': ['Completed', 'In Progress', 'Not Started'],
                'values': [
                    all_progress.filter(completed=True).count(),
                    all_progress.filter(completed=False, progress__gt=0).count(),
                    all_progress.filter(completed=False, progress__lte=0).count(),
                ],
            },
            'module_coverage': {
                'labels': [
                    'Modules Completed by All Guides',
                    'Modules Not Yet Completed by All Guides'
                ],
                'values': [
                    modules_completed_by_all_guides,
                    max(total_modules - modules_completed_by_all_guides, 0),
                ],
            },
            'summary': {
                'courses': len(all_courses),
                'modules': total_modules,
                'avg_progress': round(
                    normalize_progress_value(
                        all_progress.aggregate(avg=Avg('progress'))['avg'] or 0
                    )
                ),
            },
        }
    }
    for course in all_courses:
        course_progress = all_progress.filter(course=course)
        course_modules = course.modules.count()
        if total_guides > 0:
            course_completed_by_all_guides = (
                Module.objects.filter(course=course)
                .annotate(
                    completed_guides=Count(
                        'moduleprogress__user',
                        filter=Q(
                            moduleprogress__completed=True,
                            moduleprogress__user__in=guide_qs,
                        ),
                        distinct=True,
                    )
                )
                .filter(completed_guides=total_guides)
                .count()
            )
        else:
            course_completed_by_all_guides = 0
        datasets[str(course.id)] = {
            'label': get_display_title(course.title, fallback=f'Course {course.id}'),
            'learner_status': {
                'labels': ['Completed', 'In Progress', 'Not Started'],
                'values': [
                    course_progress.filter(completed=True).count(),
                    course_progress.filter(completed=False, progress__gt=0).count(),
                    course_progress.filter(completed=False, progress__lte=0).count(),
                ],
            },
            'module_coverage': {
                'labels': [
                    'Modules Completed by All Guides',
                    'Modules Not Yet Completed by All Guides'
                ],
                'values': [
                    course_completed_by_all_guides,
                    max(course_modules - course_completed_by_all_guides, 0),
                ],
            },
            'summary': {
                'courses': 1,
                'modules': course_modules,
                'avg_progress': round(
                    normalize_progress_value(
                        course_progress.aggregate(avg=Avg('progress'))['avg'] or 0
                    )
                ),
            },
        }
    if selected_course_id not in datasets:
        selected_course_id = 'all'
    return {
        'selected_course_id': selected_course_id,
        'course_options': [
            {'id': 'all', 'label': 'All Courses'},
            *[
                {
                    'id': str(course.id),
                    'label': get_display_title(course.title, fallback=f'Course {course.id}')
                }
                for course in all_courses
            ],
        ],
        'datasets': datasets,
    }

def get_backup_summary():
    """Get backup system summary/status."""
    setting = get_or_create_backup_setting()
    latest_history = BackupHistory.objects.order_by('-created_at').first()
    
    # Count backups in Firebase
    firebase_count = 0
    try:
        safe_prefix = (setting.firebase_backup_prefix or 'system_backups').strip('/') + '/'
        bucket = storage.bucket()
        blobs = [blob for blob in bucket.list_blobs(prefix=safe_prefix) if blob.name.endswith('.json')]
        firebase_count = len(blobs)
    except Exception:
        firebase_count = 0
    
    total_history = BackupHistory.objects.count()
    successful = BackupHistory.objects.filter(status='success').count()
    failed = BackupHistory.objects.filter(status='failed').count()
    
    return {
        'auto_backup_enabled': setting.auto_backup_enabled,
        'last_backup_at': latest_history.created_at if latest_history else None,
        'last_backup_status': latest_history.status if latest_history else 'none',
        'last_backup_destination': latest_history.destination if latest_history else '',
        'firebase_backup_count': firebase_count,
        'total_backups_logged': total_history,
        'successful_backups': successful,
        'failed_backups': failed,
        'next_backup_at': setting.next_backup_at,
        'retention_count': setting.firebase_retention_count,
    }

def generate_pdf_backup_history():
    """Generate PDF report of backup history."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    story = []
    styles = getSampleStyleSheet()
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#2d6a4f'),
        spaceAfter=12,
        alignment=1
    )
    story.append(Paragraph('Backup History Report', title_style))
    story.append(Paragraph(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['Normal']))
    story.append(Spacer(1, 0.3*inch))
    
    # Get backup history (last 50 records)
    history = BackupHistory.objects.order_by('-created_at')[:50]
    
    # Create table data
    data = [['Date & Time', 'Action', 'Status', 'Destination', 'Size', 'User']]
    for h in history:
        status_text = '✓ Success' if h.status == 'success' else '✗ Failed'
        size_text = pretty_bytes(h.file_size_bytes) if h.file_size_bytes else 'N/A'
        data.append([
            h.created_at.strftime('%Y-%m-%d\n%H:%M:%S'),
            h.get_action_type_display(),
            status_text,
            h.destination or h.blob_path[-20:] if h.blob_path else 'N/A',
            size_text,
            h.triggered_by.username if h.triggered_by else 'System',
        ])
    
    # Create and style table
    table = Table(data, colWidths=[1.2*inch, 1*inch, 0.8*inch, 1.2*inch, 0.8*inch, 1*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2d6a4f')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f8f5')]),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
    ]))
    story.append(table)
    
    doc.build(story)
    buffer.seek(0)
    return buffer

def generate_pdf_audit_trail():
    """Generate PDF report of backup audit trail."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    story = []
    styles = getSampleStyleSheet()
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#2d6a4f'),
        spaceAfter=12,
        alignment=1
    )
    story.append(Paragraph('Backup Audit Trail Report', title_style))
    story.append(Paragraph(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', styles['Normal']))
    story.append(Spacer(1, 0.3*inch))
    
    # Get audit trail (last 100 records)
    audit = BackupAuditLog.objects.order_by('-created_at')[:100]
    
    # Create table data
    data = [['Date & Time', 'User', 'Action', 'Metadata']]
    for log in audit:
        data.append([
            log.created_at.strftime('%Y-%m-%d\n%H:%M:%S'),
            log.user.username if log.user else 'Unknown',
            log.action if log.action else 'N/A',
            (log.metadata[:30] + '...') if len(log.metadata or '') > 30 else log.metadata or '',
        ])
    
    # Create and style table
    table = Table(data, colWidths=[1.3*inch, 1.2*inch, 1.5*inch, 1.3*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d9494a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fef0f0')]),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
    ]))
    story.append(table)
    
    doc.build(story)
    buffer.seek(0)
    return buffer

def is_staff_or_admin(user):
    """Check if user is staff or admin"""
    return user.is_staff or user.is_superuser


def generate_temporary_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_unique_username_from_email(email):
    base = (email.split('@')[0] or 'parkguide').lower().replace(' ', '')
    base = ''.join(ch for ch in base if ch.isalnum() or ch in ('_', '.')) or 'parkguide'
    candidate = base
    index = 1
    while CustomUser.objects.filter(username=candidate).exists():
        candidate = f'{base}{index}'
        index += 1
    return candidate

def get_guide_queryset():
    """Get queryset of guide users (not staff or superuser)"""
    return CustomUser.objects.filter(is_staff=False, is_superuser=False, is_active=True)

def build_learning_insight_data(selected_course_id=None):
    """Build learning insights data  for new course/chapter/quiz structure"""
    selected_course_id = str(selected_course_id or 'all')
    
    all_courses = Course.objects.all().order_by('code')
    total_enrollments = CourseEnrollment.objects.count()
    
    datasets = {
        'all': {
            'label': 'All Courses',
            'enrollment_status': {
                'labels': ['Completed', 'In Progress', 'Enrolled'],
                'values': [
                    CourseEnrollment.objects.filter(status='completed').count(),
                    CourseEnrollment.objects.filter(status='in_progress').count(),
                    CourseEnrollment.objects.filter(status='enrolled').count(),
                ],
            },
            'summary': {
                'courses': all_courses.count(),
                'chapters': Chapter.objects.count(),
                'lessons': Lesson.objects.count(),
                'quizzes': Quiz.objects.count(),
                'avg_progress': round(
                    CourseEnrollment.objects.aggregate(avg=Avg('progress_percentage'))['avg'] or 0, 1
                ),
            },
        }
    }
    
    # Per-course breakdown
    for course in all_courses:
        course_enrollments = CourseEnrollment.objects.filter(course=course)
        
        datasets[str(course.id)] = {
            'label': course.code + ": " + get_title_text(course.title, 'en', 'Untitled'),
            'enrollment_status': {
                'labels': ['Completed', 'In Progress', 'Enrolled'],
                'values': [
                    course_enrollments.filter(status='completed').count(),
                    course_enrollments.filter(status='in_progress').count(),
                    course_enrollments.filter(status='enrolled').count(),
                ],
            },
            'summary': {
                'courses': 1,
                'chapters': course.chapters.count(),
                'lessons': Lesson.objects.filter(chapter__course=course).count(),
                'quizzes': Quiz.objects.filter(chapter__course=course).count(),
                'avg_progress': round(
                    course_enrollments.aggregate(avg=Avg('progress_percentage'))['avg'] or 0, 1
                ),
            },
        }
    
    if selected_course_id not in datasets:
        selected_course_id = 'all'
    
    return {
        'selected_course_id': selected_course_id,
        'course_options': [
            {'id': 'all', 'label': 'All 5 Courses'},
            *[
                {
                    'id': str(course.id),
                    'label': f"{course.code}: {get_title_text(course.title, 'en', 'Untitled')[:30]}"
                }
                for course in all_courses
            ],
        ],
        'datasets': datasets,
    }

def get_guide_queryset():
    """Get queryset of guide users (not staff or superuser) - redundant but kept for compatibility"""
    return CustomUser.objects.filter(is_staff=False, is_superuser=False, is_active=True)

def get_dashboard_stats(request):
    """Get comprehensive dashboard statistics for the home page"""
    
    # User statistics
    total_users = CustomUser.objects.count()
    active_users = CustomUser.objects.filter(is_active=True).count()
    guides = get_guide_queryset()
    active_guides = guides.count()
    new_this_week = CustomUser.objects.filter(
        date_joined__gte=timezone.now() - timedelta(days=7)
    ).count()
    
    # Course statistics (updated for new 5-course structure)
    total_courses = Course.objects.count()
    total_chapters = Chapter.objects.count()
    total_lessons = Lesson.objects.count()
    total_quizzes = Quiz.objects.count()
    
    # Calculate average progress across all users
    enrollments = CourseEnrollment.objects.all()
    avg_course_progress = enrollments.aggregate(avg=Avg('progress_percentage'))['avg'] or 0
    
    # Course completion stats
    completed_enrollments = enrollments.filter(status='completed').count()
    in_progress_enrollments = enrollments.filter(status='in_progress').count()
    enrolled_only = enrollments.filter(status='enrolled').count()
    
    # Badge statistics
    pending_badges = UserBadge.objects. filter(status='pending').count()
    granted_badges = UserBadge.objects.filter(status='granted').count()
    total_badges = Badge.objects.count()
    
    # Notification statistics
    total_notifications = Notification.objects.count()
    unread_notifications = Notification.objects.filter(admin_seen_at__isnull=True).count()
    
    # Learning insights - use the previous function
    learning_insights = build_learning_insight_data()
    
    # Backup summary
    backup_summary = get_backup_summary()
    
    # Prerequisite tracking (new 5-course system)
    courses_with_prereqs = Course.objects.filter(prerequisites__isnull=False).distinct().count()
    courses_entry_point = Course.objects.filter(prerequisites__isnull=True).count()
    
    # Recent activity (user enrollments and progress)
    recent_enrollments = CourseEnrollment.objects.select_related('user', 'course').order_by('-enrollment_date')[:10]
    recent_activity = []
    for enrollment in recent_enrollments:
        recent_activity.append({
            'type': 'enrollment',
            'user': enrollment.user,
            'course': enrollment.course,
            'timestamp': enrollment.enrollment_date,
            'status': enrollment.status,
        })
    
    # Recent completed chapters
    recent_progress = ChapterProgress.objects.filter(is_complete=True).select_related('user', 'chapter').order_by('-updated_at')[:5]
    for progress in recent_progress:
        recent_activity.append({
            'type': 'chapter_complete',
            'user': progress.user,
            'chapter': progress.chapter,
            'timestamp': progress.updated_at,
        })
    
    # Sort by timestamp
    recent_activity.sort(key=lambda x: x['timestamp'], reverse=True)
    recent_activity = recent_activity[:15]  # Keep top 15
    
    context = {
        'stats': {
            'total_users': total_users,
            'active_users': active_users,
            'new_this_week': new_this_week,
            'active_guides': active_guides,
        },
        'course_stats': {
            'total_courses': total_courses,
            'total_chapters': total_chapters,
            'total_lessons': total_lessons,
            'total_quizzes': total_quizzes,
            'avg_progress': round(avg_course_progress, 1),
            'completed': completed_enrollments,
            'in_progress': in_progress_enrollments,
            'enrolled': enrolled_only,
            'total_modules': total_chapters,  # For backward compatibility with template
            'prerequisite_enabled_courses': courses_with_prereqs,
            'entry_point_courses': courses_entry_point,
        },
        'badge_stats': {
            'pending_approvals': pending_badges,
            'granted': granted_badges,
            'total_badges': total_badges,
        },
        'notification_stats': {
            'total': total_notifications,
            'unread': unread_notifications,
        },
        'learning_insights': learning_insights,
        'backup_summary': backup_summary,
        'recent_activity': recent_activity,
    }
    
    return context

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
            phone_number = request.POST.get('phone_number', '').strip()
            birthdate = request.POST.get('birthdate') or None
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
                    phone_number=phone_number,
                    birthdate=birthdate,
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
        
        if action == 'edit_user':
            user_id = request.POST.get('user_id')
            target_user = CustomUser.objects.filter(id=user_id).first()
            if not target_user:
                messages.error(request, 'User not found.')
                return redirect('dashboard:users')
            username = request.POST.get('username', '').strip()
            email = request.POST.get('email', '').strip().lower()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            phone_number = request.POST.get('phone_number', '').strip()
            birthdate = request.POST.get('birthdate') or None
            is_staff = request.POST.get('is_staff') == 'on'
            if not username or not email:
                messages.error(request, 'Username and email are required.')
            elif CustomUser.objects.exclude(id=target_user.id).filter(username=username).exists():
                messages.error(request, 'A user with this username already exists.')
            elif CustomUser.objects.exclude(id=target_user.id).filter(email=email).exists():
                messages.error(request, 'A user with this email already exists.')
            elif target_user.id == request.user.id and request.user.is_superuser and not is_staff:
                messages.error(request, 'You cannot remove your own staff access.')
            else:
                target_user.username = username
                target_user.email = email
                target_user.first_name = first_name
                target_user.last_name = last_name
                target_user.phone_number = phone_number
                target_user.birthdate = birthdate
                target_user.is_staff = is_staff
                target_user.save()
                messages.success(request, f'User {target_user.username} updated successfully.')
            return redirect('dashboard:users')

        if action == 'delete_user':
            user_id = request.POST.get('user_id')
            target_user = CustomUser.objects.filter(id=user_id).first()
            if not target_user:
                messages.error(request, 'User not found.')
                return redirect('dashboard:users')
            if target_user.id == request.user.id:
                messages.error(request, 'You cannot delete your own account.')
                return redirect('dashboard:users')
            if target_user.is_superuser:
                messages.error(request, 'Superuser accounts cannot be deleted from this panel.')
                return redirect('dashboard:users')
            username = target_user.username
            try:
                with transaction.atomic():
                    # Delete remote secure-file blobs first
                    for secure_file in target_user.secure_files.all():
                        try:
                            delete_secure_blob(secure_file.s3_key)
                        except ImproperlyConfigured:
                            pass
                    target_user.delete()
                messages.success(request, f'User {username} deleted successfully.')
            except Exception as exc:
                messages.error(request, f'Could not delete user: {exc}')
            return redirect('dashboard:users')

        if action == 'approve_application':
            next_url = request.POST.get('next') or reverse('dashboard:users')
            application_id = request.POST.get('application_id')
            notes = (request.POST.get('admin_notes') or '').strip()
            keep_cv = request.POST.get('keep_cv') == 'on'
            application = AccountApplication.objects.filter(id=application_id).first()

            if not application:
                messages.error(request, 'Application not found.')
                return redirect(next_url)

            if application.status != AccountApplication.STATUS_PENDING:
                messages.error(request, 'Only pending applications can be approved.')
                return redirect(next_url)

            if CustomUser.objects.filter(email=application.email).exists():
                messages.error(request, 'A user with this email already exists.')
                return redirect(next_url)

            temp_password = generate_temporary_password()
            username = generate_unique_username_from_email(application.email)
            name_parts = application.full_name.split(' ', 1)
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else ''

            new_user = CustomUser.objects.create_user(
                username=username,
                email=application.email,
                password=temp_password,
                first_name=first_name,
                last_name=last_name,
                phone_number=application.phone_number,
                birthdate=application.birthdate,
                is_staff=False,
                must_change_password=True,
            )

            application.approved_user = new_user
            application.mark_reviewed(
                reviewer=request.user,
                status=AccountApplication.STATUS_APPROVED,
                notes=notes,
            )
            application.save(update_fields=['approved_user', 'updated_at'])

            send_mail(
                subject='Park Guide application approved',
                message=(
                    f'Hello {application.full_name},\n\n'
                    'Your Park Guide application has been approved.\n\n'
                    f'Temporary account details:\n'
                    f'Email: {application.email}\n'
                    f'Password: {temp_password}\n\n'
                    'Please sign in and change your password on your first login.\n\n'
                    'Regards,\nPark Guide Team'
                ),
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[application.email],
                fail_silently=True,
            )

            if not keep_cv and application.cv_storage_key:
                try:
                    delete_application_cv(application.cv_storage_key)
                except Exception:
                    pass
                application.cv_storage_key = ''
                application.cv_original_name = ''
                application.cv_content_type = ''
                application.cv_size = 0
                application.save(update_fields=['cv_storage_key', 'cv_original_name', 'cv_content_type', 'cv_size', 'updated_at'])

            messages.success(request, f'Application approved and temporary account created for {application.email}.')
            return redirect(next_url)

        if action == 'deny_application':
            next_url = request.POST.get('next') or reverse('dashboard:users')
            application_id = request.POST.get('application_id')
            notes = (request.POST.get('admin_notes') or '').strip()
            keep_cv = request.POST.get('keep_cv') == 'on'
            application = AccountApplication.objects.filter(id=application_id).first()

            if not application:
                messages.error(request, 'Application not found.')
                return redirect(next_url)

            if application.status != AccountApplication.STATUS_PENDING:
                messages.error(request, 'Only pending applications can be denied.')
                return redirect(next_url)

            application.mark_reviewed(
                reviewer=request.user,
                status=AccountApplication.STATUS_DENIED,
                notes=notes,
            )

            send_mail(
                subject='Park Guide application update',
                message=(
                    f'Hello {application.full_name},\n\n'
                    'We are sorry to deny your application at this time.\n\n'
                    'You may apply again in the future with updated details.\n\n'
                    'Regards,\nPark Guide Team'
                ),
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[application.email],
                fail_silently=True,
            )

            if not keep_cv and application.cv_storage_key:
                try:
                    delete_application_cv(application.cv_storage_key)
                except Exception:
                    pass
                application.cv_storage_key = ''
                application.cv_original_name = ''
                application.cv_content_type = ''
                application.cv_size = 0
                application.save(update_fields=['cv_storage_key', 'cv_original_name', 'cv_content_type', 'cv_size', 'updated_at'])

            messages.success(request, f'Application denied for {application.email}.')
            return redirect(next_url)
    users = CustomUser.objects.all().order_by('-date_joined')
    pending_applications = AccountApplication.objects.filter(status=AccountApplication.STATUS_PENDING).order_by('-created_at')
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        users = users.filter(
            Q(username__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(phone_number__icontains=search_query)
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
        'pending_applications': pending_applications,
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
def dashboard_requests(request):
    """Preview account application requests in a dedicated page."""
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip().lower()

    applications = AccountApplication.objects.select_related('reviewed_by', 'approved_user').order_by('-created_at')

    if search_query:
        applications = applications.filter(
            Q(full_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(phone_number__icontains=search_query)
        )

    if status_filter in {
        AccountApplication.STATUS_PENDING,
        AccountApplication.STATUS_APPROVED,
        AccountApplication.STATUS_DENIED,
    }:
        applications = applications.filter(status=status_filter)

    page = request.GET.get('page', 1)
    per_page = 20
    total_requests = applications.count()
    start = (int(page) - 1) * per_page
    end = start + per_page
    requests_paginated = applications[start:end]
    total_pages = (total_requests + per_page - 1) // per_page

    context = {
        'requests': requests_paginated,
        'search_query': search_query,
        'status_filter': status_filter,
        'current_page': int(page),
        'total_pages': total_pages,
        'total_requests': total_requests,
        'stats': {
            'pending': AccountApplication.objects.filter(status=AccountApplication.STATUS_PENDING).count(),
            'approved': AccountApplication.objects.filter(status=AccountApplication.STATUS_APPROVED).count(),
            'denied': AccountApplication.objects.filter(status=AccountApplication.STATUS_DENIED).count(),
        },
    }
    return render(request, 'dashboard/requests.html', context)


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_request_cv(request, application_id):
    application = get_object_or_404(AccountApplication, id=application_id)
    if not application.cv_storage_key:
        messages.error(request, 'CV is not available for this request.')
        return redirect('dashboard:requests')

    try:
        signed_url = generate_application_cv_url(application.cv_storage_key)
    except Exception:
        messages.error(request, 'Unable to generate CV preview link right now.')
        return redirect('dashboard:requests')

    return HttpResponseRedirect(signed_url)


def _reset_enrollment_progress(enrollment):
    """Reset all tracked progress for a single enrollment."""
    target_course_id = enrollment.course_id
    user = enrollment.user

    LessonProgress.objects.filter(
        user=user,
        lesson__chapter__course_id=target_course_id,
    ).delete()
    ChapterProgress.objects.filter(
        user=user,
        chapter__course_id=target_course_id,
    ).delete()
    PracticeAttempt.objects.filter(
        user=user,
        exercise__chapter__course_id=target_course_id,
    ).delete()
    QuizAttempt.objects.filter(
        user=user,
        quiz__chapter__course_id=target_course_id,
    ).delete()

    enrollment.status = 'enrolled'
    enrollment.started_date = None
    enrollment.completed_date = None
    enrollment.completed_chapters = 0
    enrollment.total_chapters = enrollment.course.chapters.count()
    enrollment.progress_percentage = 0
    enrollment.final_score = None
    enrollment.total_time_spent = 0
    enrollment.save(update_fields=[
        'status', 'started_date', 'completed_date', 'completed_chapters',
        'total_chapters', 'progress_percentage', 'final_score',
        'total_time_spent', 'updated_at'
    ])


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_enrollments(request):
    """Manage course enrollments, status, and progress resets."""
    if request.method == 'POST':
        action = request.POST.get('action')
        enrollment_id = request.POST.get('enrollment_id')
        next_url = request.POST.get('next') or reverse('dashboard:enrollments')
        enrollment = CourseEnrollment.objects.filter(id=enrollment_id).select_related('user', 'course').first()

        if not enrollment:
            messages.error(request, 'Enrollment not found.')
            return redirect(next_url)

        with transaction.atomic():
            if action == 'update_status':
                new_status = request.POST.get('status')
                valid_statuses = {choice[0] for choice in CourseEnrollment.STATUS_CHOICES}
                if new_status not in valid_statuses:
                    messages.error(request, 'Invalid enrollment status.')
                else:
                    enrollment.status = new_status
                    if new_status == 'completed':
                        enrollment.completed_date = enrollment.completed_date or timezone.now()
                        enrollment.progress_percentage = 100
                        enrollment.completed_chapters = enrollment.course.chapters.count()
                    elif new_status == 'in_progress':
                        enrollment.started_date = enrollment.started_date or timezone.now()
                        enrollment.completed_date = None
                    elif new_status == 'enrolled':
                        enrollment.started_date = None
                        enrollment.completed_date = None
                    enrollment.save()
                    messages.success(
                        request,
                        f'Updated {enrollment.user.username} in {enrollment.course.code} to {enrollment.get_status_display()}.'
                    )
            elif action == 'reset_progress':
                _reset_enrollment_progress(enrollment)
                messages.success(
                    request,
                    f'Reset progress for {enrollment.user.username} in {enrollment.course.code}.'
                )
            elif action == 'delete_enrollment':
                course_code = enrollment.course.code
                username = enrollment.user.username
                enrollment.delete()
                messages.success(request, f'Removed enrollment for {username} from {course_code}.')
            else:
                messages.error(request, 'Unknown enrollment action.')

        return redirect(next_url)

    enrollments = CourseEnrollment.objects.select_related('user', 'course').order_by('-updated_at')
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()
    course_filter = request.GET.get('course', '').strip()

    if search_query:
        enrollments = enrollments.filter(
            Q(user__username__icontains=search_query) |
            Q(user__email__icontains=search_query) |
            Q(course__code__icontains=search_query) |
            Q(course__title__en__icontains=search_query)
        )

    if status_filter:
        enrollments = enrollments.filter(status=status_filter)

    if course_filter:
        enrollments = enrollments.filter(course_id=course_filter)

    paginator = Paginator(enrollments, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    stats_qs = CourseEnrollment.objects.all()
    stats = {
        'total': stats_qs.count(),
        'completed': stats_qs.filter(status='completed').count(),
        'in_progress': stats_qs.filter(status='in_progress').count(),
        'enrolled': stats_qs.filter(status='enrolled').count(),
        'avg_progress': round(stats_qs.aggregate(avg=Avg('progress_percentage'))['avg'] or 0, 1),
    }

    context = {
        'page_obj': page_obj,
        'enrollments': page_obj.object_list,
        'search_query': search_query,
        'status_filter': status_filter,
        'course_filter': course_filter,
        'status_choices': CourseEnrollment.STATUS_CHOICES,
        'course_options': Course.objects.order_by('code'),
        'stats': stats,
    }
    return render(request, 'dashboard/enrollments.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_courses(request):
    """Course management page - NEW: integrated with 5-course hierarchy system"""

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'bulk_delete_courses':
            course_ids = [value for value in request.POST.getlist('course_ids') if value]
            if not course_ids:
                messages.error(request, 'Select at least one course to delete.')
                return redirect('dashboard:courses')

            qs = Course.objects.filter(id__in=course_ids)
            selected_count = qs.count()
            if selected_count == 0:
                messages.error(request, 'No matching courses found to delete.')
                return redirect('dashboard:courses')

            qs.delete()
            messages.success(request, f'Deleted {selected_count} course(s).')
            return redirect('dashboard:courses')

        messages.error(request, 'Unknown course action.')
        return redirect('dashboard:courses')
    
    # Get all courses with their relationships
    courses = Course.objects.prefetch_related('chapters', 'prerequisites').all().order_by('code')
    
    # Calculate statistics for each course
    course_data = []
    for course in courses:
        chapters = course.chapters.count()
        lessons = Lesson.objects.filter(chapter__course=course).count()
        quizzes = Quiz.objects.filter(chapter__course=course).count()
        total_practice = 0  # Can be extended for practice exercises
        enrollments = CourseEnrollment.objects.filter(course=course).count()
        completed_enrollments = CourseEnrollment.objects.filter(course=course, status='completed').count()
        
        # Prerequisite info
        has_prerequisites = course.prerequisites.exists()
        prerequisite_list = list(course.prerequisites.values_list('code', flat=True))
        
        course_data.append({
            'course': course,
            'chapters': chapters,
            'lessons': lessons,
            'quizzes': quizzes,
            'practice_exercises': total_practice,
            'enrollments': enrollments,
            'completed': completed_enrollments,
            'has_prerequisites': has_prerequisites,
            'prerequisites': prerequisite_list,
            'completion_rate': (completed_enrollments / enrollments * 100) if enrollments > 0 else 0,
        })
    
    # Aggregate stats
    total_enrollments = CourseEnrollment.objects.count()
    total_completed = CourseEnrollment.objects.filter(status='completed').count()
    
    context = {
        'course_data': course_data,
        'stats': {
            'total_courses': courses.count(),
            'total_chapters': Chapter.objects.all().count(),
            'total_lessons': Lesson.objects.all().count(),
            'total_quizzes': Quiz.objects.all().count(),
            'total_enrollments': total_enrollments,
            'total_completed': total_completed,
            'overall_completion_rate': (total_completed / total_enrollments * 100) if total_enrollments > 0 else 0,
            'prerequisite_enabled_courses': courses.filter(prerequisites__isnull=False).distinct().count(),
        },
        'all_courses': courses,
    }
    return render(request, 'dashboard/courses.html', context)


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_course_details(request, course_id):
    """Course details page with inline editing for chapters, lessons, quizzes, exercises"""
    course = get_object_or_404(Course, pk=course_id)
    
    # Get all chapters with their content
    chapters = course.chapters.all().order_by('order').prefetch_related(
        'lessons',
        'quizzes', 
        'practice_exercises'
    )
    
    # Build detailed structure and calculate totals
    course_content = []
    total_lessons = 0
    total_quizzes = 0
    total_exercises = 0
    
    for chapter in chapters:
        lessons = list(chapter.lessons.all().order_by('order'))
        quizzes = list(chapter.quizzes.all().order_by('order'))
        exercises = list(chapter.practice_exercises.all().order_by('order'))
        
        total_lessons += len(lessons)
        total_quizzes += len(quizzes)
        total_exercises += len(exercises)
        
        course_content.append({
            'chapter': chapter,
            'lesson_count': len(lessons),
            'quiz_count': len(quizzes),
            'exercise_count': len(exercises),
            'lessons': lessons,
            'quizzes': quizzes,
            'exercises': exercises,
            'total_content': len(lessons) + len(quizzes) + len(exercises),
        })
    
    # Extract multilingual title for display
    course_title = course.title
    if isinstance(course_title, dict):
        course_title_display = get_title_text(course_title, 'en', 'Untitled')
    else:
        course_title_display = str(course_title)
    
    context = {
        'course': course,
        'course_title_display': course_title_display,
        'course_content': course_content,
        'total_lessons': total_lessons,
        'total_quizzes': total_quizzes,
        'total_exercises': total_exercises,
        'chapter_form': ChapterForm(),
        'lesson_form': LessonForm(),
        'quiz_form': QuizForm(),
        'exercise_form': PracticeExerciseForm(),
        'prerequisites': course.prerequisites.all(),
    }
    return render(request, 'dashboard/course_details.html', context)


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_course_create(request):
    """Create a new course"""
    if request.method == 'POST':
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save()
            messages.success(request, f'Course "{course.title.get("en")}" created successfully!')
            return redirect('dashboard:courses')
    else:
        form = CourseForm()
    
    context = {
        'form': form,
        'title': 'Create Course',
    }
    return render(request, 'dashboard/course_form.html', context)


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_course_edit(request, course_id):
    """Edit an existing course"""
    course = get_object_or_404(Course, pk=course_id)
    
    if request.method == 'POST':
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            course = form.save()
            messages.success(request, f'Course "{course.title.get("en")}" updated successfully!')
            return redirect('dashboard:courses')
    else:
        form = CourseForm(instance=course)
    
    context = {
        'form': form,
        'title': 'Edit Course',
        'course': course,
    }
    return render(request, 'dashboard/course_form.html', context)


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_course_import(request):
    """Import courses from JSON"""
    if request.method == 'POST':
        form = CourseImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # Get JSON content
                if request.FILES.get('json_file'):
                    json_content = request.FILES['json_file'].read().decode('utf-8')
                else:
                    json_content = form.cleaned_data['json_text']
                
                courses_data = json.loads(json_content)
                
                # Handle both list and single object
                if not isinstance(courses_data, list):
                    courses_data = [courses_data]
                
                imported_count = 0
                errors = []
                
                with transaction.atomic():
                    for course_data in courses_data:
                        try:
                            # Extract prerequisites if provided
                            prerequisites = course_data.pop('prerequisites', [])
                            
                            # Prepare course data
                            course_defaults = {
                                'title': {
                                    'en': course_data.get('name') or course_data.get('title', {}).get('en', 'Untitled'),
                                    'ms': course_data.get('title', {}).get('ms', ''),
                                    'zh': course_data.get('title', {}).get('zh', ''),
                                },
                                'description': {
                                    'en': course_data.get('description', ''),
                                    'ms': '',
                                    'zh': '',
                                },
                                'thumbnail': course_data.get('thumbnail') or course_data.get('thumbnail_image', ''),
                                'is_published': course_data.get('is_published', True),
                            }
                            
                            # Create or update course
                            course, created = Course.objects.update_or_create(
                                code=course_data.get('code'),
                                defaults=course_defaults
                            )
                            
                            # Set prerequisites
                            if prerequisites:
                                prerequisite_courses = Course.objects.filter(code__in=prerequisites)
                                course.prerequisites.set(prerequisite_courses)
                            
                            # Import chapters if provided
                            chapters_data = course_data.get('chapters', [])
                            for chapter_data in chapters_data:
                                chapter_title = chapter_data.get('title')
                                if isinstance(chapter_title, str):
                                    chapter_title = {'en': chapter_title, 'ms': '', 'zh': ''}
                                
                                chapter_desc = chapter_data.get('description', '')
                                if isinstance(chapter_desc, str):
                                    chapter_desc = {'en': chapter_desc, 'ms': '', 'zh': ''}
                                
                                chapter, _ = Chapter.objects.update_or_create(
                                    course=course,
                                    order=chapter_data.get('order', 1),
                                    defaults={
                                        'title': chapter_title,
                                        'description': chapter_desc,
                                    }
                                )
                                
                                # Import lessons if provided
                                lessons_data = chapter_data.get('lessons', [])
                                for lesson_data in lessons_data:
                                    lesson_title = lesson_data.get('title')
                                    if isinstance(lesson_title, str):
                                        lesson_title = {'en': lesson_title, 'ms': '', 'zh': ''}
                                    
                                    lesson_content = lesson_data.get('content', '')
                                    if isinstance(lesson_content, str):
                                        lesson_content = {'en': lesson_content, 'ms': '', 'zh': ''}
                                    
                                    Lesson.objects.update_or_create(
                                        chapter=chapter,
                                        order=lesson_data.get('order', 1),
                                        defaults={
                                            'title': lesson_title,
                                            'content': lesson_content,
                                        }
                                    )
                            
                            imported_count += 1
                        except Exception as e:
                            errors.append(f"Error importing course {course_data.get('code')}: {str(e)}")
                
                if errors:
                    for error in errors:
                        messages.warning(request, error)
                
                if imported_count > 0:
                    messages.success(request, f'Successfully imported {imported_count} course(s)!')
                    return redirect('dashboard:courses')
                else:
                    messages.error(request, 'No courses were imported.')
                    
            except json.JSONDecodeError as e:
                messages.error(request, f'Invalid JSON: {str(e)}')
            except Exception as e:
                messages.error(request, f'Import error: {str(e)}')
    else:
        form = CourseImportForm()
    
    context = {
        'form': form,
        'title': 'Import Courses',
    }
    return render(request, 'dashboard/course_import.html', context)


# ============================================================================
# INLINE EDITING API ENDPOINTS (for dashboard content management)
# ============================================================================

@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_chapter_save(request, course_id=None, chapter_id=None):
    """Create or update chapter - AJAX endpoint"""
    try:
        if chapter_id:
            chapter = get_object_or_404(Chapter, pk=chapter_id)
            course = chapter.course
        else:
            course = get_object_or_404(Course, pk=course_id)
            chapter = Chapter(course=course)
        
        form = ChapterForm(request.POST, instance=chapter)
        if form.is_valid():
            chapter = form.save()
            return JsonResponse({
                'success': True,
                'id': chapter.id,
                'message': f'Chapter "{chapter.title.get("en")}" saved successfully'
            })
        else:
            return JsonResponse({
                'success': False,
                'errors': form.errors
            }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_chapter_delete(request, chapter_id):
    """Delete chapter - AJAX endpoint"""
    try:
        chapter = get_object_or_404(Chapter, pk=chapter_id)
        course = chapter.course
        chapter_name = get_title_text(chapter.title, 'en', 'Chapter')
        chapter.delete()
        return JsonResponse({
            'success': True,
            'message': f'Chapter "{chapter_name}" deleted successfully'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_lesson_save(request, chapter_id=None, lesson_id=None):
    """Create or update lesson - AJAX endpoint"""
    try:
        if lesson_id:
            lesson = get_object_or_404(Lesson, pk=lesson_id)
            chapter = lesson.chapter
        else:
            chapter = get_object_or_404(Chapter, pk=chapter_id)
            lesson = Lesson(chapter=chapter)
        
        form = LessonForm(request.POST, instance=lesson)
        if form.is_valid():
            lesson = form.save()
            return JsonResponse({
                'success': True,
                'id': lesson.id,
                'message': f'Lesson "{lesson.title.get("en")}" saved successfully'
            })
        else:
            return JsonResponse({
                'success': False,
                'errors': form.errors
            }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_lesson_delete(request, lesson_id):
    """Delete lesson - AJAX endpoint"""
    try:
        lesson = get_object_or_404(Lesson, pk=lesson_id)
        lesson_name = get_title_text(lesson.title, 'en', 'Lesson')
        lesson.delete()
        return JsonResponse({
            'success': True,
            'message': f'Lesson "{lesson_name}" deleted successfully'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_quiz_save(request, chapter_id=None, quiz_id=None):
    """Create or update quiz - AJAX endpoint"""
    try:
        if quiz_id:
            quiz = get_object_or_404(Quiz, pk=quiz_id)
            chapter = quiz.chapter
        else:
            chapter = get_object_or_404(Chapter, pk=chapter_id)
            quiz = Quiz(chapter=chapter)
        
        form = QuizForm(request.POST, instance=quiz)
        if form.is_valid():
            quiz = form.save()
            return JsonResponse({
                'success': True,
                'id': quiz.id,
                'message': f'Quiz "{quiz.title.get("en")}" saved successfully'
            })
        else:
            return JsonResponse({
                'success': False,
                'errors': form.errors
            }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_quiz_delete(request, quiz_id):
    """Delete quiz - AJAX endpoint"""
    try:
        quiz = get_object_or_404(Quiz, pk=quiz_id)
        quiz_name = get_title_text(quiz.title, 'en', 'Quiz')
        quiz.delete()
        return JsonResponse({
            'success': True,
            'message': f'Quiz "{quiz_name}" deleted successfully'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_exercise_save(request, chapter_id=None, exercise_id=None):
    """Create or update practice exercise - AJAX endpoint"""
    try:
        if exercise_id:
            exercise = get_object_or_404(PracticeExercise, pk=exercise_id)
            chapter = exercise.chapter
        else:
            chapter = get_object_or_404(Chapter, pk=chapter_id)
            exercise = PracticeExercise(chapter=chapter)
        
        form = PracticeExerciseForm(request.POST, instance=exercise)
        if form.is_valid():
            exercise = form.save()
            return JsonResponse({
                'success': True,
                'id': exercise.id,
                'message': f'Exercise "{exercise.title.get("en")}" saved successfully'
            })
        else:
            return JsonResponse({
                'success': False,
                'errors': form.errors
            }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def api_exercise_delete(request, exercise_id):
    """Delete practice exercise - AJAX endpoint"""
    try:
        exercise = get_object_or_404(PracticeExercise, pk=exercise_id)
        exercise_name = get_title_text(exercise.title, 'en', 'Exercise')
        exercise.delete()
        return JsonResponse({
            'success': True,
            'message': f'Exercise "{exercise_name}" deleted successfully'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        }, status=400)


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_course_delete(request, course_id):
    """Delete a course"""
    course = get_object_or_404(Course, pk=course_id)
    
    if request.method == 'POST':
        course_name = f"{course.code} - {get_title_text(course.title, 'en', 'Untitled')}"
        course.delete()
        messages.success(request, f'Course "{course_name}" deleted successfully!')
        return redirect('dashboard:courses')
    
    context = {
        'course': course,
        'title': 'Confirm Delete',
    }
    return render(request, 'dashboard/course_confirm_delete.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_progress(request):
    """User progress tracking page - Shows individual student progress with summary view"""
    
    # Get all users with enrollments
    users_with_enrollments = CustomUser.objects.filter(
        course_enrollments__isnull=False
    ).distinct().select_related().prefetch_related(
        'course_enrollments__course',
        'course_enrollments__course__chapters'
    )
    
    # Search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        users_with_enrollments = users_with_enrollments.filter(
            Q(username__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )
    
    # Sorting
    sort_by = request.GET.get('sort', '-last_login')
    if sort_by == 'alphabetical':
        users_with_enrollments = users_with_enrollments.order_by('username')
    elif sort_by == 'recent':
        users_with_enrollments = users_with_enrollments.order_by('-last_login')
    elif sort_by == 'progress':
        # Sort by average progress (requires annotation)
        from django.db.models import Avg as AvgAggregate
        users_with_enrollments = users_with_enrollments.annotate(
            avg_progress=AvgAggregate('course_enrollments__progress_percentage')
        ).order_by('-avg_progress')
    else:
        users_with_enrollments = users_with_enrollments.order_by('-last_login')
    
    # Build student summary data
    student_rows = []
    for user in users_with_enrollments:
        enrollments = CourseEnrollment.objects.filter(user=user).select_related('course').order_by('-updated_at')
        
        if not enrollments.exists():
            continue
        
        # Get latest enrollment
        latest_enrollment = enrollments.first()
        
        # Calculate stats
        completed_count = enrollments.filter(status='completed').count()
        in_progress_count = enrollments.filter(status='in_progress').count()
        total_enrollments = enrollments.count()
        avg_progress = enrollments.aggregate(avg=Avg('progress_percentage'))['avg'] or 0
        
        # Get chapter progress for latest course
        chapter_progress = ChapterProgress.objects.filter(
            user=user,
            chapter__course=latest_enrollment.course
        ).aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(is_complete=True))
        )
        
        latest_activity = None
        if user.last_login:
            latest_activity = user.last_login
        
        student_rows.append({
            'user': user,
            'latest_enrollment': latest_enrollment,
            'total_enrollments': total_enrollments,
            'completed_count': completed_count,
            'in_progress_count': in_progress_count,
            'avg_progress': round(avg_progress, 1),
            'latest_activity': latest_activity,
            'chapter_progress': chapter_progress,
            'all_enrollments': list(enrollments[:5]),  # Latest 5 for quick preview
        })
    
    # Pagination
    page = request.GET.get('page', 1)
    per_page = 12  # Shows more students per page
    paginator = Paginator(student_rows, per_page)
    total_pages = paginator.num_pages
    total = paginator.count
    
    try:
        page_obj = paginator.page(page)
        paginated_students = page_obj.object_list
    except:
        page_obj = paginator.page(1)
        paginated_students = page_obj.object_list
    
    # Overall statistics
    total_users = CustomUser.objects.filter(course_enrollments__isnull=False).distinct().count()
    total_enrollments = CourseEnrollment.objects.count()
    completed = CourseEnrollment.objects.filter(status='completed').count()
    in_progress = CourseEnrollment.objects.filter(status='in_progress').count()
    enrolled = CourseEnrollment.objects.filter(status='enrolled').count()
    avg_progress = CourseEnrollment.objects.aggregate(avg=Avg('progress_percentage'))['avg'] or 0
    
    # Get most active students (based on recent activity)
    most_active = CustomUser.objects.filter(
        course_enrollments__isnull=False
    ).distinct().order_by('-last_login')[:5]
    
    context = {
        'students': paginated_students,
        'total': total,
        'current_page': page_obj.number,
        'total_pages': total_pages,
        'search_query': search_query,
        'sort_by': sort_by,
        'stats': {
            'avg_progress': round(avg_progress, 1),
            'completed_enrollments': completed,
            'in_progress_enrollments': in_progress,
            'enrolled_only': enrolled,
            'total_enrollments': total_enrollments,
            'total_active_users': total_users,
        },
        'most_active': most_active,
    }
    return render(request, 'dashboard/progress.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_student_progress(request, user_id):
    """
    API endpoint to get full progress details for a specific student.
    Used by modals/detail views to show complete enrollment history.
    """
    user = get_object_or_404(CustomUser, pk=user_id)
    enrollments = CourseEnrollment.objects.filter(user=user).select_related('course').prefetch_related('course__chapters').order_by('-updated_at')
    
    enrollment_details = []
    for enrollment in enrollments:
        # Get chapter progress
        chapter_progress_qs = ChapterProgress.objects.filter(
            user=user,
            chapter__course=enrollment.course
        ).select_related('chapter')
        
        completed_chapters = chapter_progress_qs.filter(is_complete=True).count()
        total_chapters = enrollment.course.chapters.count()
        
        # Calculate progress
        progress_pct = (completed_chapters / total_chapters * 100) if total_chapters > 0 else 0
        
        # Get quiz performance
        quiz_attempts = QuizAttempt.objects.filter(
            user=user,
            quiz__chapter__course=enrollment.course
        ) if hasattr(QuizAttempt, 'objects') else []
        
        avg_quiz_score = quiz_attempts.aggregate(avg=Avg('score'))['avg'] if quiz_attempts.exists() else None
        
        enrollment_details.append({
            'enrollment': {
                'id': enrollment.id,
                'course_code': enrollment.course.code,
                'course_title': get_title_text(enrollment.course.title, 'en', 'Untitled'),
                'status': enrollment.get_status_display(),
                'progress_percentage': enrollment.progress_percentage,
                'enrollment_date': enrollment.enrollment_date.isoformat() if enrollment.enrollment_date else None,
                'completed_date': enrollment.completed_date.isoformat() if enrollment.completed_date else None,
                'final_score': enrollment.final_score,
            },
            'chapters': {
                'completed': completed_chapters,
                'total': total_chapters,
                'percentage': progress_pct,
            },
            'quiz_avg': round(avg_quiz_score, 1) if avg_quiz_score else None,
        })
    
    return JsonResponse({
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'name': f"{user.first_name} {user.last_name}".strip(),
            'last_login': user.last_login.isoformat() if user.last_login else None,
        },
        'enrollments': enrollment_details,
        'summary': {
            'total_courses': len(enrollments),
            'completed': len([e for e in enrollments if e.status == 'completed']),
            'in_progress': len([e for e in enrollments if e.status == 'in_progress']),
            'enrolled': len([e for e in enrollments if e.status == 'enrolled']),
            'avg_progress': round(enrollments.aggregate(avg=Avg('progress_percentage'))['avg'] or 0, 1),
        }
    })


@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def dashboard_reset_student_progress(request, user_id):
    """
    Reset a student's learning progress.
    If enrollment_id is provided, only reset that course enrollment.
    Otherwise reset all learning progress for the student.
    """
    user = get_object_or_404(CustomUser, pk=user_id)

    payload = {}
    if request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except json.JSONDecodeError:
            payload = {}

    enrollment_id = payload.get('enrollment_id')
    enrollments = CourseEnrollment.objects.filter(user=user)
    target_course_ids = None

    if enrollment_id:
        enrollment = get_object_or_404(enrollments.select_related('course'), pk=enrollment_id)
        enrollments = enrollments.filter(pk=enrollment.pk)
        target_course_ids = [enrollment.course_id]
        reset_scope = f'course {enrollment.course.code}'
    else:
        target_course_ids = list(enrollments.values_list('course_id', flat=True))
        reset_scope = 'all courses'

    with transaction.atomic():
        lesson_progress_qs = LessonProgress.objects.filter(user=user)
        chapter_progress_qs = ChapterProgress.objects.filter(user=user)
        practice_attempt_qs = PracticeAttempt.objects.filter(user=user)
        quiz_attempt_qs = QuizAttempt.objects.filter(user=user)

        if target_course_ids:
            lesson_progress_qs = lesson_progress_qs.filter(lesson__chapter__course_id__in=target_course_ids)
            chapter_progress_qs = chapter_progress_qs.filter(chapter__course_id__in=target_course_ids)
            practice_attempt_qs = practice_attempt_qs.filter(exercise__chapter__course_id__in=target_course_ids)
            quiz_attempt_qs = quiz_attempt_qs.filter(quiz__chapter__course_id__in=target_course_ids)

        lesson_progress_deleted = lesson_progress_qs.count()
        chapter_progress_deleted = chapter_progress_qs.count()
        practice_attempt_deleted = practice_attempt_qs.count()
        quiz_attempt_deleted = quiz_attempt_qs.count()

        lesson_progress_qs.delete()
        chapter_progress_qs.delete()
        practice_attempt_qs.delete()
        quiz_attempt_qs.delete()

        for enrollment in enrollments.select_related('course'):
            enrollment.status = 'enrolled'
            enrollment.started_date = None
            enrollment.completed_date = None
            enrollment.completed_chapters = 0
            enrollment.total_chapters = enrollment.course.chapters.count()
            enrollment.progress_percentage = 0
            enrollment.final_score = None
            enrollment.total_time_spent = 0
            enrollment.save(update_fields=[
                'status', 'started_date', 'completed_date', 'completed_chapters',
                'total_chapters', 'progress_percentage', 'final_score',
                'total_time_spent', 'updated_at'
            ])

    return JsonResponse({
        'ok': True,
        'message': f'Reset progress for {user.username} ({reset_scope}).',
        'deleted': {
            'lesson_progress': lesson_progress_deleted,
            'chapter_progress': chapter_progress_deleted,
            'practice_attempts': practice_attempt_deleted,
            'quiz_attempts': quiz_attempt_deleted,
        }
    })


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_badges(request):
    """Badge management, tracking, and admin approval page"""
    from user_progress.services import (
        revoke_badge, re_grant_badge, grant_course_completion_badge,
        check_and_grant_achievement_badges, create_or_update_course_badge,
        get_badge_leaderboard, get_course_badge_requirement_count,
        get_badge_image_access_url, get_default_badge_blob_path,
    )
    
    # Handle AJAX badge details request
    if request.method == 'GET' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        action = request.GET.get('action')
        
        if action == 'badge_details':
            badge_id = request.GET.get('badge_id')
            badge = Badge.objects.filter(id=badge_id).first()
            if not badge:
                return JsonResponse({'ok': False, 'error': 'Badge not found.'}, status=404)
            
            users = UserBadge.objects.filter(badge=badge).select_related('user', 'awarded_by', 'revoked_by').order_by('-awarded_at')
            
            badge_data = []
            for user_badge in users:
                badge_data.append({
                    'id': user_badge.id,
                    'user_id': user_badge.user.id,
                    'username': user_badge.user.username,
                    'email': user_badge.user.email,
                    'status': user_badge.status,
                    'is_awarded': user_badge.is_awarded,
                    'awarded_at': user_badge.awarded_at.strftime('%b %d, %Y %H:%M') if user_badge.awarded_at else '',
                    'awarded_by': user_badge.awarded_by.username if user_badge.awarded_by else 'System',
                    'revoked_at': user_badge.revoked_at.strftime('%b %d, %Y %H:%M') if user_badge.revoked_at else '',
                    'revoked_by': user_badge.revoked_by.username if user_badge.revoked_by else '',
                })
            
            return JsonResponse({
                'ok': True,
                'badge': {
                    'id': badge.id,
                    'name': badge.name,
                    'description': badge.description or '',
                    'raw_badge_image_url': badge.badge_image_url or '',
                    'badge_image_url': get_badge_image_access_url(badge.badge_image_url),
                    'badge_image_source': badge.badge_image_source or '',
                    'skills_awarded': badge.skills_awarded or [],
                    'lesson_highlights': badge.lesson_highlights or [],
                    'course_id': badge.course_id or '',
                    'required_completed_modules': badge.required_completed_modules,
                    'course_title': badge.course.title.get('en', 'Course') if badge.course else '',
                    'is_major': badge.is_major_badge,
                    'users': badge_data,
                }
            })

        elif action == 'default_badge_image':
            course_id = (request.GET.get('course_id') or '').strip()
            course = Course.objects.filter(id=course_id).first()
            if not course:
                return JsonResponse({'ok': False, 'error': 'Course not found.'}, status=404)

            storage_path = get_default_badge_blob_path(course)
            if not storage_path:
                return JsonResponse({'ok': False, 'error': 'No default Firebase badge image configured for this course.'}, status=404)

            return JsonResponse({
                'ok': True,
                'storage_path': storage_path,
                'signed_url': get_badge_image_access_url(storage_path),
            })
        
        elif action == 'user_badges':
            user_id = request.GET.get('user_id')
            user = CustomUser.objects.filter(id=user_id).first()
            if not user:
                return JsonResponse({'ok': False, 'error': 'User not found.'}, status=404)
            
            user_badges = UserBadge.objects.filter(user=user).select_related('badge', 'revoked_by').order_by('-awarded_at')
            
            badges_data = []
            for user_badge in user_badges:
                badges_data.append({
                    'id': user_badge.id,
                    'badge_id': user_badge.badge.id,
                    'name': user_badge.badge.name,
                    'badge_image_url': get_badge_image_access_url(user_badge.badge.badge_image_url),
                    'status': user_badge.status,
                    'is_awarded': user_badge.is_awarded,
                    'is_major': user_badge.badge.is_major_badge,
                    'awarded_at': user_badge.awarded_at.strftime('%b %d, %Y') if user_badge.awarded_at else '',
                    'revoked_at': user_badge.revoked_at.strftime('%b %d, %Y') if user_badge.revoked_at else '',
                })
            
            return JsonResponse({
                'ok': True,
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                },
                'badges': badges_data,
            })

    # Handle POST actions
    if request.method == 'POST':
        action = request.POST.get('action')
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if action == 'sync_course_badges':
            created_or_updated = 0
            for course in Course.objects.all().order_by('code'):
                create_or_update_course_badge(course)
                created_or_updated += 1
            messages.success(request, f'Generated or refreshed {created_or_updated} course badge(s).')
            return redirect('dashboard:badges')

        if action == 'save_badge':
            badge_id = (request.POST.get('badge_id') or '').strip()
            name = (request.POST.get('name') or '').strip()
            description = (request.POST.get('description') or '').strip()
            course_id = (request.POST.get('course_id') or '').strip()
            badge_image_url = (request.POST.get('badge_image_url') or '').strip()
            badge_image_storage_path = (request.POST.get('badge_image_storage_path') or '').strip()
            badge_image_source = (request.POST.get('badge_image_source') or '').strip()
            skills_awarded = [line.strip() for line in (request.POST.get('skills_awarded') or '').splitlines() if line.strip()]
            lesson_highlights = [line.strip() for line in (request.POST.get('lesson_highlights') or '').splitlines() if line.strip()]

            if not name:
                messages.error(request, 'Badge name is required.')
                return redirect('dashboard:badges')

            course = Course.objects.filter(id=course_id).first() if course_id else None
            required_completed_modules = request.POST.get('required_completed_modules')
            try:
                required_completed_modules = int(required_completed_modules or 1)
            except (TypeError, ValueError):
                messages.error(request, 'Required completed modules must be a number.')
                return redirect('dashboard:badges')

            if course and not skills_awarded:
                skills_awarded = list(course.chapters.order_by('order').values_list('title__en', flat=True))
            if course and not lesson_highlights:
                lesson_highlights = list(course.chapters.order_by('order', 'lessons__order').values_list('lessons__title__en', flat=True))
            if course and not description:
                description = (course.description or {}).get('en', '').strip()
            final_badge_image_value = badge_image_storage_path or badge_image_url
            if course and not final_badge_image_value:
                final_badge_image_value = get_default_badge_blob_path(course) or course.thumbnail or ''
            if final_badge_image_value and not badge_image_source:
                badge_image_source = final_badge_image_value
            if course and required_completed_modules == 1:
                required_completed_modules = get_course_badge_requirement_count(course)

            badge = Badge.objects.filter(id=badge_id).first() if badge_id else None
            badge_with_same_name = Badge.objects.filter(name=name).first()

            if badge_id and not badge:
                messages.error(request, 'Badge not found.')
                return redirect('dashboard:badges')

            if badge and badge_with_same_name and badge_with_same_name.id != badge.id:
                messages.error(request, f'A badge named "{name}" already exists.')
                return redirect('dashboard:badges')

            # If the edit form loses its hidden badge_id, fall back to the existing
            # unique badge row for that name instead of attempting a duplicate insert.
            if not badge:
                badge = badge_with_same_name or Badge()

            badge.name = name
            badge.description = description
            badge.badge_image_url = final_badge_image_value
            badge.badge_image_source = badge_image_source
            badge.skills_awarded = skills_awarded
            badge.lesson_highlights = lesson_highlights
            badge.course = course
            badge.required_completed_modules = max(required_completed_modules, 1)
            badge.required_badges_count = 0
            badge.is_major_badge = False
            badge.is_active = True
            badge.auto_approve_when_eligible = False
            badge.save()

            messages.success(request, f'Badge {"updated" if badge_id else "created"}: {badge.name}')
            return redirect('dashboard:badges')

        if action == 'approve_all_badges':
            pending_badges_qs = UserBadge.objects.filter(status='pending').select_related('badge', 'user')
            approved_total = 0
            for user_badge in pending_badges_qs:
                user_badge.status = 'granted'
                user_badge.is_awarded = True
                user_badge.awarded_by = request.user
                user_badge.revoked_at = None
                user_badge.revoked_by = None
                user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by', 'revoked_at', 'revoked_by'])
                from user_progress.services import notify_badge_granted_to_user
                notify_badge_granted_to_user(user_badge, admin_user=request.user)
                approved_total += 1

            if approved_total:
                messages.success(request, f'Approved {approved_total} pending badge request(s).')
            else:
                messages.info(request, 'No pending badge requests to approve.')
            return redirect('dashboard:badges')

        if action == 'delete_badge':
            badge_id = (request.POST.get('badge_id') or '').strip()
            badge = Badge.objects.filter(id=badge_id).first()
            if not badge:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Badge not found.'}, status=404)
                messages.error(request, 'Badge not found.')
                return redirect('dashboard:badges')

            badge_name = badge.name
            badge.delete()
            if is_ajax:
                return JsonResponse({'ok': True, 'message': f'Badge deleted: {badge_name}'})
            messages.success(request, f'Badge deleted: {badge_name}')
            return redirect('dashboard:badges')

        if action == 'delete_badges_bulk':
            badge_ids = [value for value in request.POST.getlist('badge_ids') if value]
            if not badge_ids:
                messages.error(request, 'Select at least one badge to delete.')
                return redirect('dashboard:badges')

            qs = Badge.objects.filter(id__in=badge_ids)
            selected_count = qs.count()
            if selected_count == 0:
                messages.error(request, 'No matching badges found to delete.')
                return redirect('dashboard:badges')

            qs.delete()
            messages.success(request, f'Deleted {selected_count} badge(s).')
            return redirect('dashboard:badges')
        
        if action == 'revoke_badge':
            user_badge_id = request.POST.get('user_badge_id')
            try:
                user_badge = UserBadge.objects.get(id=user_badge_id)
                revoke_badge(user_badge.user, user_badge.badge, request.user)
                
                if is_ajax:
                    return JsonResponse({'ok': True, 'message': f'Badge revoked: {user_badge.badge.name}'})
                
                messages.success(request, f'Badge revoked: {user_badge.badge.name}')
                return redirect('dashboard:badges')
            except UserBadge.DoesNotExist:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Badge not found.'}, status=404)
                messages.error(request, 'Badge not found.')
                return redirect('dashboard:badges')
        
        elif action == 're_grant_badge':
            user_badge_id = request.POST.get('user_badge_id')
            try:
                user_badge = UserBadge.objects.get(id=user_badge_id)
                re_grant_badge(user_badge.user, user_badge.badge, request.user)
                
                if is_ajax:
                    return JsonResponse({'ok': True, 'message': f'Badge re-granted: {user_badge.badge.name}'})
                
                messages.success(request, f'Badge re-granted: {user_badge.badge.name}')
                return redirect('dashboard:badges')
            except UserBadge.DoesNotExist:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Badge not found.'}, status=404)
                messages.error(request, 'Badge not found.')
                return redirect('dashboard:badges')
        
        elif action == 'manual_grant_course_badge':
            user_id = request.POST.get('user_id')
            course_id = request.POST.get('course_id')
            try:
                user = CustomUser.objects.get(id=user_id)
                course = Course.objects.get(id=course_id)
                grant_course_completion_badge(user, course)
                
                if is_ajax:
                    return JsonResponse({'ok': True, 'message': f'Course badge granted: {course.code}'})
                
                messages.success(request, f'Course badge granted: {course.code}')
                return redirect('dashboard:badges')
            except (CustomUser.DoesNotExist, Course.DoesNotExist) as e:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': str(e)}, status=404)
                messages.error(request, str(e))
                return redirect('dashboard:badges')
        
        elif action == 'approve_badge':
            """Approve a pending badge and notify user"""
            from notifications.services import create_notification_for_user
            user_badge_id = request.POST.get('user_badge_id')
            try:
                user_badge = UserBadge.objects.get(id=user_badge_id)
                
                if user_badge.status != 'pending':
                    if is_ajax:
                        return JsonResponse({'ok': False, 'error': 'Badge is not pending.'}, status=400)
                    messages.error(request, 'Badge is not pending.')
                    return redirect('dashboard:badges')
                
                # Approve the badge
                user_badge.status = 'granted'
                user_badge.is_awarded = True
                user_badge.awarded_by = request.user
                user_badge.revoked_at = None
                user_badge.revoked_by = None
                user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by', 'revoked_at', 'revoked_by'])
                
                # Notify user about approval
                from user_progress.services import notify_badge_granted_to_user
                notify_badge_granted_to_user(user_badge, admin_user=request.user)
                
                if is_ajax:
                    return JsonResponse({
                        'ok': True,
                        'message': f'Badge approved: {user_badge.badge.name}',
                        'pending_total': UserBadge.objects.filter(status='pending').count(),
                        'badge_id': user_badge.badge.id,
                        'badge_granted': UserBadge.objects.filter(
                            badge=user_badge.badge,
                            status='granted',
                            is_awarded=True,
                        ).count(),
                        'badge_pending': UserBadge.objects.filter(
                            badge=user_badge.badge,
                            status='pending',
                        ).count(),
                    })
                
                messages.success(request, f'✓ Badge approved: {user_badge.badge.name} | Notification sent to {user_badge.user.email}')
                return redirect('dashboard:badges')
            except UserBadge.DoesNotExist:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Badge not found.'}, status=404)
                messages.error(request, 'Badge not found.')
                return redirect('dashboard:badges')
        
        elif action == 'reject_badge':
            """Reject a pending badge"""
            user_badge_id = request.POST.get('user_badge_id')
            try:
                user_badge = UserBadge.objects.get(id=user_badge_id)
                
                if user_badge.status != 'pending':
                    if is_ajax:
                        return JsonResponse({'ok': False, 'error': 'Badge is not pending.'}, status=400)
                    messages.error(request, 'Badge is not pending.')
                    return redirect('dashboard:badges')
                
                # Reject the badge
                user_badge.status = 'rejected'
                user_badge.is_awarded = False
                user_badge.awarded_by = None
                user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by'])
                
                # Optional: Notify user about rejection
                from notifications.services import create_notification_for_user
                create_notification_for_user(
                    user=user_badge.user,
                    title=f'Badge request declined: {user_badge.badge.name}',
                    description='Your badge request was not approved.',
                    full_text=(f'Your request for the badge "{user_badge.badge.name}" was reviewed and declined '
                              f'by {request.user.email}. You may request review again later.'),
                    created_by=request.user,
                    related_user=user_badge.user,
                )
                
                if is_ajax:
                    return JsonResponse({
                        'ok': True,
                        'message': f'Badge rejected: {user_badge.badge.name}',
                        'pending_total': UserBadge.objects.filter(status='pending').count(),
                        'badge_id': user_badge.badge.id,
                        'badge_granted': UserBadge.objects.filter(
                            badge=user_badge.badge,
                            status='granted',
                            is_awarded=True,
                        ).count(),
                        'badge_pending': UserBadge.objects.filter(
                            badge=user_badge.badge,
                            status='pending',
                        ).count(),
                    })
                
                messages.success(request, f'✓ Badge rejected: {user_badge.badge.name}')
                return redirect('dashboard:badges')
            except UserBadge.DoesNotExist:
                if is_ajax:
                    return JsonResponse({'ok': False, 'error': 'Badge not found.'}, status=404)
                messages.error(request, 'Badge not found.')
                return redirect('dashboard:badges')

    # Get all badges with stats
    badges = Badge.objects.annotate(
        granted_badges=Count('user_badges', filter=Q(user_badges__status='granted', user_badges__is_awarded=True)),
        total_revoked=Count('user_badges', filter=Q(user_badges__revoked_at__isnull=False)),
        pending_approvals=Count('user_badges', filter=Q(user_badges__status='pending')),
    ).order_by('-granted_badges', 'name')
    
    # Show all regular badges in the main grid, including older/general badges
    # that exist without a linked course.
    course_badges = badges.filter(is_major_badge=False)
    achievement_badges = badges.filter(is_major_badge=True)
    
    # Get badge stats
    all_user_badges = UserBadge.objects.all()
    stats = {
        'total_badges': Badge.objects.count(),
        'course_badges': Badge.objects.filter(is_major_badge=False).count(),
        'achievement_badges': Badge.objects.filter(is_major_badge=True).count(),
        'total_granted': all_user_badges.filter(status='granted', is_awarded=True).count(),
        'total_revoked': all_user_badges.filter(revoked_at__isnull=False).count(),
        'pending_approvals': all_user_badges.filter(status='pending').count(),
    }
    
    # Get pending badges for admin approval
    pending_user_badges = UserBadge.objects.filter(status='pending').select_related('user', 'badge').order_by('-awarded_at')
    
    # Get badge leaderboard
    leaderboard = get_badge_leaderboard(limit=10)
    
    # Pagination for courses (if showing course-specific badges)
    page = request.GET.get('page', 1)
    per_page = 15
    paginator = Paginator(course_badges, per_page)
    total_pages = paginator.num_pages
    
    try:
        page_obj = paginator.page(page)
        paginated_badges = page_obj.object_list
    except:
        page_obj = paginator.page(1)
        paginated_badges = page_obj.object_list

    for badge in paginated_badges:
        badge.display_badge_image_url = get_badge_image_access_url(badge.badge_image_url)
    
    course_options = list(Course.objects.all().order_by('code').prefetch_related('modules', 'chapters'))
    for course in course_options:
        course.badge_requirement = get_course_badge_requirement_count(course)
        course.badge_default_image_storage_path = get_default_badge_blob_path(course)
        course.badge_default_image_url = get_badge_image_access_url(course.badge_default_image_storage_path)

    context = {
        'badges': paginated_badges,
        'achievement_badges': achievement_badges,
        'pending_badges': pending_user_badges,
        'total_pending': pending_user_badges.count(),
        'leaderboard': leaderboard,
        'stats': stats,
        'current_page': page_obj.number,
        'total_pages': total_pages,
        'courses': course_options,
    }
    
    return render(request, 'dashboard/badges.html', context)

def get_guide_queryset():
    return CustomUser.objects.filter(is_active=True, is_staff=False, is_superuser=False).order_by('username')

def decorate_notification_for_dashboard(item):
    if item.audience_type == Notification.AUDIENCE_ADMINS:
        item.display_audience = 'Admins'
    elif item.audience_type == Notification.AUDIENCE_ALL_GUIDES:
        item.display_audience = 'All Guides'
    else:
        if item.recipient_count == 1:
            item.display_audience = '1 Selected Guide'
        else:
            item.display_audience = f'{item.recipient_count} Selected Guides'
    if item.tracking_type == Notification.TRACKING_ADMIN_SHARED:
        item.display_tracking = 'Any admin can mark as read'
    elif item.tracking_type == Notification.TRACKING_INFO_ONLY:
        item.display_tracking = 'No response needed'
    elif item.tracking_type == Notification.TRACKING_USER_READ:
        item.display_tracking = f'{item.read_count}/{item.recipient_count} Confirmed read'
    elif item.tracking_type == Notification.TRACKING_USER_ACK:
        item.display_tracking = f'{item.read_count}/{item.recipient_count} User acknowledged'
    else:
        item.display_tracking = '-'
    if item.related_user:
        full_name = item.related_user.get_full_name().strip()
        label_name = full_name or item.related_user.username
        item.display_regarding = f'{label_name} ({item.related_user.email})'
    else:
        item.display_regarding = ''
    return item

def get_admin_notifications_queryset():
    return (
        Notification.objects
        .filter(show_in_header=True)
        .select_related('created_by', 'admin_seen_by', 'related_user')
        .annotate(
            recipient_count=Count('recipients', distinct=True),
            read_count=Count('recipients', filter=Q(recipients__is_read=True), distinct=True),
            unread_count=Count('recipients', filter=Q(recipients__is_read=False), distinct=True),
        )
        .order_by('-sent_at')
    )


def serialize_admin_notification(item):
    decorate_notification_for_dashboard(item)

    created_by_name = 'System'
    if item.created_by:
        full_name = item.created_by.get_full_name().strip()
        created_by_name = full_name or item.created_by.username

    admin_seen_by_name = ''
    if item.admin_seen_by:
        full_name = item.admin_seen_by.get_full_name().strip()
        admin_seen_by_name = full_name or item.admin_seen_by.username

    return {
        'id': item.id,
        'title': item.title,
        'description': item.description or '',
        'full_text': item.full_text,
        'display_audience': item.display_audience,
        'display_tracking': item.display_tracking,
        'display_regarding': getattr(item, 'display_regarding', ''),
        'sent_human': f'{timesince(item.sent_at)} ago',
        'sent_full': item.sent_at.strftime('%b %d, %Y %H:%M'),
        'created_by': created_by_name,
        'admin_read': bool(item.admin_seen_at),
        'admin_seen_by': admin_seen_by_name,
    }


def get_admin_notification_summary():
    visible_qs = Notification.objects.filter(show_in_header=True)
    total_count = visible_qs.count()
    unread_count = visible_qs.filter(admin_seen_at__isnull=True).count()
    return {
        'total_count': total_count,
        'unread_count': unread_count,
        'all_read': total_count > 0 and unread_count == 0,
    }

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
            audience_type = request.POST.get('audience_type', Notification.AUDIENCE_ALL_GUIDES)
            tracking_type = request.POST.get('tracking_type', Notification.TRACKING_INFO_ONLY)
            selected_user_ids = request.POST.getlist('selected_users')
            if not title or not full_text:
                messages.error(request, 'Title and full message are required.')
                return redirect('dashboard:notifications')
            guide_qs = get_guide_queryset()
            recipients = []
            if audience_type == Notification.AUDIENCE_ADMINS:
                tracking_type = Notification.TRACKING_ADMIN_SHARED
            elif audience_type == Notification.AUDIENCE_ALL_GUIDES:
                recipients = list(guide_qs)
                if tracking_type == Notification.TRACKING_ADMIN_SHARED:
                    tracking_type = Notification.TRACKING_INFO_ONLY
            elif audience_type == Notification.AUDIENCE_SELECTED_GUIDES:
                recipients = list(guide_qs.filter(id__in=selected_user_ids))
                if tracking_type == Notification.TRACKING_ADMIN_SHARED:
                    tracking_type = Notification.TRACKING_INFO_ONLY
                if not recipients:
                    messages.error(request, 'Please select at least one active guide.')
                    return redirect('dashboard:notifications')
            else:
                messages.error(request, 'Invalid audience type.')
                return redirect('dashboard:notifications')
            with transaction.atomic():
                notification = Notification.objects.create(title=title, description=description, full_text=full_text, audience_type=audience_type, tracking_type=tracking_type, created_by=request.user)
                if recipients:
                    UserNotification.objects.bulk_create([UserNotification(user=user, notification=notification) for user in recipients])
            
            # Send push notifications to recipients
            if recipients:
                try:
                    print(f"\n=== DASHBOARD: Sending push notification to {len(recipients)} users ===")
                    send_push_to_users(recipients, title, description or full_text)
                    print("Push notifications sent successfully from dashboard")
                except Exception as e:
                    print(f"Failed to send push notifications from dashboard: {str(e)}")
                    messages.warning(request, f'Notification created but push sending failed: {str(e)}')
            
            if audience_type == Notification.AUDIENCE_ADMINS:
                messages.success(request, 'Notification sent to admins.')
            elif audience_type == Notification.AUDIENCE_ALL_GUIDES:
                messages.success(request, f'Notification sent to all guides ({len(recipients)} users).')
            else:
                messages.success(request, f'Notification sent to {len(recipients)} selected guide(s).')
            return redirect('dashboard:notifications')
        elif action == 'mark_notification_seen':
            notification_id = request.POST.get('notification_id')
            notification = Notification.objects.filter(id=notification_id).first()
            if not notification:
                return JsonResponse({'ok': False, 'error': 'Notification not found'}, status=404)
            if notification.admin_seen_at is None:
                now = timezone.now()
                notification.admin_seen_at = now
                notification.admin_seen_by = request.user
                notification.save(update_fields=['admin_seen_at', 'admin_seen_by'])
            return JsonResponse({
                'ok': True,
                'admin_seen_at': notification.admin_seen_at.isoformat() if notification.admin_seen_at else None,
                'admin_seen_by': request.user.username,
            })
        elif action == 'delete_notification':
            notification_id = request.POST.get('notification_id')
            notification = get_object_or_404(Notification, id=notification_id)
            title = notification.title
            notification.delete()
            messages.success(request, f'Notification "{title}" deleted.')
            return redirect('dashboard:notifications')
    search_query = request.GET.get('search', '').strip()
    read_status = request.GET.get('status', '').strip()
    notifications_qs = (
        Notification.objects
        .select_related('created_by', 'admin_seen_by', 'related_user')
        .annotate(
            recipient_count=Count('recipients', distinct=True),
            read_count=Count('recipients', filter=Q(recipients__is_read=True), distinct=True),
            unread_count=Count('recipients', filter=Q(recipients__is_read=False), distinct=True),
        )
        .order_by('-sent_at')
    )
    if search_query:
        notifications_qs = notifications_qs.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(full_text__icontains=search_query) |
            Q(recipients__user__username__icontains=search_query) |
            Q(recipients__user__email__icontains=search_query)
        ).distinct()
    if read_status == 'unread':
        notifications_qs = notifications_qs.filter(admin_seen_at__isnull=True)
    elif read_status == 'read':
        notifications_qs = notifications_qs.filter(admin_seen_at__isnull=False)
    paginator = Paginator(notifications_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    notifications = list(page_obj.object_list.prefetch_related('recipients__user'))
    for item in notifications:
        decorate_notification_for_dashboard(item)
        item.recipient_usernames_csv = ', '.join(item.recipients.all().values_list('user__username', flat=True))
    context = {
        'page_obj': page_obj,
        'notifications': notifications,
        'search_query': search_query,
        'selected_status': read_status,
        'guide_users': get_guide_queryset(),
        'stats': {
            'total_notifications': Notification.objects.count(),
            'unread': Notification.objects.filter(admin_seen_at__isnull=True).count(),
            'sent_this_week': Notification.objects.filter(
                sent_at__gte=timezone.now() - timedelta(days=7)
            ).count(),
        }
    }
    return render(request, 'dashboard/notifications.html', context)

@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["GET"])
def header_notifications_feed(request):
    notifications = [
        serialize_admin_notification(item)
        for item in get_admin_notifications_queryset()
    ]

    return JsonResponse({
        'ok': True,
        'notifications': notifications,
        **get_admin_notification_summary(),
    })
    
@login_required
@user_passes_test(is_staff_or_admin)
@require_http_methods(["POST"])
def header_notifications_action(request):
    action = request.POST.get('action', '').strip()
    now = timezone.now()
    if action == 'mark_one_read':
        notification_id = request.POST.get('notification_id')
        notification = get_object_or_404(Notification, id=notification_id)
        if notification.admin_seen_at is None:
            notification.admin_seen_at = now
            notification.admin_seen_by = request.user
            notification.save(update_fields=['admin_seen_at', 'admin_seen_by'])
        item = get_admin_notifications_queryset().get(id=notification.id)
        return JsonResponse({
            'ok': True,
            'notification': serialize_admin_notification(item),
            **get_admin_notification_summary(),
        })
    if action == 'mark_all_read':
        Notification.objects.filter(admin_seen_at__isnull=True).update(
            admin_seen_at=now,
            admin_seen_by_id=request.user.id,
        )
        return JsonResponse({
            'ok': True,
            **get_admin_notification_summary(),
        })
    if action == 'clear_one':
        notification_id = request.POST.get('notification_id')
        notification = get_object_or_404(Notification, id=notification_id)
        notification.show_in_header = False
        notification.save(update_fields=['show_in_header'])
        return JsonResponse({
            'ok': True,
            **get_admin_notification_summary(),
        })
    if action == 'clear_all_read':
        visible_unread_exists = Notification.objects.filter(show_in_header=True,admin_seen_at__isnull=True).exists()
        if visible_unread_exists:
            return JsonResponse({
                'ok': False,
                'error': 'Cannot clear read notifications while unread notifications still exist.',
            }, status=400)
        Notification.objects.filter(show_in_header=True,admin_seen_at__isnull=False).update(show_in_header=False)
        return JsonResponse({
            'ok': True,
            **get_admin_notification_summary(),
        })
    return JsonResponse({
        'ok': False,
        'error': 'Invalid action.',
    }, status=400)

@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_secure_files(request):
    """Secure file upload and management page."""
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'upload_files':
            files = request.FILES.getlist('files')
            if not files:
                single_file = request.FILES.get('file')
                if single_file:
                    files = [single_file]
            if not files:
                messages.warning(request, 'No files selected.')
                return redirect('dashboard:secure_files')
            uploaded_count = 0
            for uploaded in files:
                try:
                    upload_file(uploaded=uploaded, owner=request.user)
                    uploaded_count += 1
                except ImproperlyConfigured as exc:
                    messages.error(request, f'Upload failed: {exc}')
                    return redirect('dashboard:secure_files')
                except Exception as exc:
                    messages.error(request, f'Upload failed for {uploaded.name}: {exc}')
                    return redirect('dashboard:secure_files')
            messages.success(request, f'Successfully uploaded {uploaded_count} file(s).')
            return redirect('dashboard:secure_files')
        if action == 'delete_file':
            file_id = request.POST.get('file_id')
            secure_file = SecureFile.objects.filter(id=file_id).first()
            if not secure_file:
                messages.error(request, 'File not found.')
                return redirect('dashboard:secure_files')
            try:
                delete_secure_blob(secure_file.s3_key)
            except ImproperlyConfigured as exc:
                messages.error(request, f'Delete failed: {exc}')
                return redirect('dashboard:secure_files')
            except Exception as exc:
                messages.error(request, f'Delete failed: {exc}')
                return redirect('dashboard:secure_files')

            secure_file.delete()
            messages.success(request, 'File deleted successfully.')
            return redirect('dashboard:secure_files')

    files_qs = SecureFile.objects.select_related('owner').all().order_by('-uploaded_at')
    page = request.GET.get('page', 1)
    per_page = 25
    total = files_qs.count()
    start = (int(page) - 1) * per_page
    end = start + per_page
    files_paginated = files_qs[start:end]
    total_pages = (total + per_page - 1) // per_page
    total_size_bytes = files_qs.aggregate(total=Sum('size'))['total'] or 0
    total_size_mb = round(total_size_bytes / (1024 * 1024), 2)

    context = {
        'files': files_paginated,
        'total': total,
        'current_page': int(page),
        'total_pages': total_pages,
        'stats': {
            'total_files': total,
            'total_size_mb': total_size_mb,
            'owners': files_qs.values('owner').distinct().count(),
        },
    }
    return render(request, 'dashboard/secure_files.html', context)


@login_required
@user_passes_test(is_staff_or_admin)
def dashboard_backups(request):
    """Create and restore JSON database backups from dashboard."""
    setting = get_or_create_backup_setting()

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()

        if action == 'export_json':
            try:
                content = build_backup_json()
                integrity_ok, integrity_error, summary = validate_backup_json_content(content)
                if not integrity_ok:
                    log_backup_history(
                        request_user=request.user,
                        action_type=BackupHistory.TYPE_EXPORT_LOCAL,
                        status=BackupHistory.STATUS_FAILED,
                        destination='local',
                        integrity_ok=False,
                        details=integrity_error,
                    )
                    messages.error(request, f'Backup export failed integrity check: {integrity_error}')
                    return redirect('dashboard:backups')

                timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
                filename = f'parkguide_backup_{timestamp}.json'
                size_bytes = len(content.encode('utf-8'))
                detail_text = f"Records: {summary.get('total_records', 0)}, Models: {summary.get('total_models', 0)}"

                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_EXPORT_LOCAL,
                    status=BackupHistory.STATUS_SUCCESS,
                    destination='local',
                    file_size_bytes=size_bytes,
                    integrity_ok=True,
                    details=detail_text,
                )
                log_backup_audit(
                    request_user=request.user,
                    action='Export local backup',
                    metadata=detail_text,
                )

                response = HttpResponse(content, content_type='application/json')
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response
            except Exception as exc:
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_EXPORT_LOCAL,
                    status=BackupHistory.STATUS_FAILED,
                    destination='local',
                    integrity_ok=False,
                    details=str(exc),
                )
                messages.error(request, f'Export failed: {exc}')
                return redirect('dashboard:backups')

        if action == 'backup_to_firebase_now':
            try:
                content = build_backup_json()
                integrity_ok, integrity_error, summary = validate_backup_json_content(content)
                if not integrity_ok:
                    raise ValueError(integrity_error)

                blob_path = upload_backup_json_to_firebase(content, setting.firebase_backup_prefix)
                removed_paths = apply_firebase_backup_retention(setting.firebase_backup_prefix, setting.firebase_retention_count)
                now = timezone.now()
                setting.last_backup_at = now
                setting.last_backup_blob_path = blob_path
                if setting.auto_backup_enabled:
                    setting.next_backup_at = compute_next_backup_time(now, setting.backup_frequency)
                setting.save(update_fields=['last_backup_at', 'last_backup_blob_path', 'next_backup_at', 'updated_at'])

                detail_text = (
                    f"Records: {summary.get('total_records', 0)}, "
                    f"Models: {summary.get('total_models', 0)}, "
                    f"Retention removed: {len(removed_paths)}"
                )
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_BACKUP_FIREBASE,
                    status=BackupHistory.STATUS_SUCCESS,
                    destination='firebase',
                    blob_path=blob_path,
                    file_size_bytes=len(content.encode('utf-8')),
                    integrity_ok=True,
                    details=detail_text,
                )
                log_backup_audit(
                    request_user=request.user,
                    action='Backup to Firebase now',
                    metadata=f'{blob_path} | {detail_text}',
                )

                messages.success(request, f'Backup uploaded to Firebase: {blob_path}')
            except Exception as exc:
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_BACKUP_FIREBASE,
                    status=BackupHistory.STATUS_FAILED,
                    destination='firebase',
                    integrity_ok=False,
                    details=str(exc),
                )
                messages.error(request, f'Firebase backup failed: {exc}')
            return redirect('dashboard:backups')

        if action == 'save_backup_settings':
            auto_enabled = request.POST.get('auto_backup_enabled') == 'on'
            frequency = request.POST.get('backup_frequency', BackupSetting.FREQUENCY_DAILY)
            prefix = request.POST.get('firebase_backup_prefix', '').strip() or 'system_backups'
            retention_count_raw = request.POST.get('firebase_retention_count', '30').strip()

            valid_frequencies = {choice[0] for choice in BackupSetting.FREQUENCY_CHOICES}
            if frequency not in valid_frequencies:
                frequency = BackupSetting.FREQUENCY_DAILY

            setting.auto_backup_enabled = auto_enabled
            setting.backup_frequency = frequency
            setting.firebase_backup_prefix = prefix
            try:
                retention_count = int(retention_count_raw)
            except ValueError:
                retention_count = 30
            setting.firebase_retention_count = max(1, min(retention_count, 1000))

            now = timezone.now()
            if auto_enabled:
                baseline = setting.last_backup_at or now
                setting.next_backup_at = compute_next_backup_time(baseline, frequency)
            else:
                setting.next_backup_at = None

            setting.save()
            log_backup_audit(
                request_user=request.user,
                action='Update backup settings',
                metadata=(
                    f'auto={setting.auto_backup_enabled}, '
                    f'frequency={setting.backup_frequency}, '
                    f'prefix={setting.firebase_backup_prefix}, '
                    f'retention={setting.firebase_retention_count}'
                ),
            )
            messages.success(request, 'Backup settings saved.')
            return redirect('dashboard:backups')

        if action == 'restore_json_dry_run':
            uploaded = request.FILES.get('backup_file')
            if not uploaded:
                messages.error(request, 'Please choose a JSON backup file for dry run.')
                return redirect('dashboard:backups')

            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.json') as tmp:
                    for chunk in uploaded.chunks():
                        tmp.write(chunk)
                    temp_path = tmp.name

                integrity_ok, integrity_error, summary = summarize_backup_json_file(temp_path)
                if not integrity_ok:
                    raise ValueError(integrity_error)

                summary_lines = [
                    f"Dry run OK: {summary.get('total_records', 0)} records across {summary.get('total_models', 0)} models.",
                ]
                top_models = sorted(summary.get('model_counts', {}).items(), key=lambda pair: pair[1], reverse=True)[:8]
                if top_models:
                    summary_lines.append('Top models: ' + ', '.join([f"{name}={count}" for name, count in top_models]))

                request.session['backup_dry_run_summary'] = summary_lines
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_RESTORE_DRY_RUN,
                    status=BackupHistory.STATUS_SUCCESS,
                    destination='local',
                    file_size_bytes=uploaded.size,
                    integrity_ok=True,
                    details=' | '.join(summary_lines),
                )
                log_backup_audit(
                    request_user=request.user,
                    action='Restore dry run',
                    metadata=' | '.join(summary_lines),
                )
                messages.success(request, summary_lines[0])
            except Exception as exc:
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_RESTORE_DRY_RUN,
                    status=BackupHistory.STATUS_FAILED,
                    destination='local',
                    file_size_bytes=getattr(uploaded, 'size', 0),
                    integrity_ok=False,
                    details=str(exc),
                )
                messages.error(request, f'Dry run failed: {exc}')
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

            return redirect('dashboard:backups')

        if action == 'run_firebase_coverage_report':
            try:
                report = generate_firebase_coverage_report()
                request.session['backup_coverage_report'] = report
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_COVERAGE_REPORT,
                    status=BackupHistory.STATUS_SUCCESS,
                    destination='firebase',
                    integrity_ok=True,
                    details=(
                        f"db={report['total_db_files']}, matched={report['matched_files']}, "
                        f"missing={report['missing_files']}"
                    ),
                )
                log_backup_audit(
                    request_user=request.user,
                    action='Run Firebase coverage report',
                    metadata=(
                        f"db={report['total_db_files']}, matched={report['matched_files']}, "
                        f"missing={report['missing_files']}"
                    ),
                )
                messages.success(request, 'Coverage report generated.')
            except Exception as exc:
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_COVERAGE_REPORT,
                    status=BackupHistory.STATUS_FAILED,
                    destination='firebase',
                    integrity_ok=False,
                    details=str(exc),
                )
                messages.error(request, f'Coverage report failed: {exc}')
            return redirect('dashboard:backups')

        if action == 'restore_json':
            uploaded = request.FILES.get('backup_file')
            if not uploaded:
                messages.error(request, 'Please choose a JSON backup file to restore.')
                return redirect('dashboard:backups')

            if not uploaded.name.lower().endswith('.json'):
                messages.error(request, 'Only .json backup files are supported.')
                return redirect('dashboard:backups')

            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.json') as tmp:
                    for chunk in uploaded.chunks():
                        tmp.write(chunk)
                    temp_path = tmp.name

                integrity_ok, integrity_error, summary = summarize_backup_json_file(temp_path)
                if not integrity_ok:
                    raise ValueError(integrity_error)

                call_command('loaddata', temp_path)
                detail_text = f"Restored {summary.get('total_records', 0)} records across {summary.get('total_models', 0)} models"
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_RESTORE,
                    status=BackupHistory.STATUS_SUCCESS,
                    destination='local',
                    file_size_bytes=uploaded.size,
                    integrity_ok=True,
                    details=detail_text,
                )
                log_backup_audit(
                    request_user=request.user,
                    action='Restore backup',
                    metadata=detail_text,
                )
                messages.success(request, 'Backup restored successfully.')
            except Exception as exc:
                log_backup_history(
                    request_user=request.user,
                    action_type=BackupHistory.TYPE_RESTORE,
                    status=BackupHistory.STATUS_FAILED,
                    destination='local',
                    file_size_bytes=getattr(uploaded, 'size', 0),
                    integrity_ok=False,
                    details=str(exc),
                )
                messages.error(request, f'Restore failed: {exc}')
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

            return redirect('dashboard:backups')

        if action == 'export_history_pdf':
            try:
                buffer = generate_pdf_backup_history()
                log_backup_audit(
                    request_user=request.user,
                    action='Export backup history as PDF',
                    metadata='',
                )
                response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
                response['Content-Disposition'] = f'attachment; filename="backup_history_{timezone.now().strftime("%Y%m%d_%H%M%S")}.pdf"'
                return response
            except Exception as exc:
                messages.error(request, f'PDF export failed: {exc}')
                return redirect('dashboard:backups')

        if action == 'export_audit_pdf':
            try:
                buffer = generate_pdf_audit_trail()
                log_backup_audit(
                    request_user=request.user,
                    action='Export audit trail as PDF',
                    metadata='',
                )
                response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
                response['Content-Disposition'] = f'attachment; filename="backup_audit_{timezone.now().strftime("%Y%m%d_%H%M%S")}.pdf"'
                return response
            except Exception as exc:
                messages.error(request, f'PDF export failed: {exc}')
                return redirect('dashboard:backups')

    context = {
        'backup_setting': setting,
        'backup_history': BackupHistory.objects.select_related('triggered_by')[:30],
        'backup_audit_logs': BackupAuditLog.objects.select_related('user')[:30],
        'coverage_report': request.session.pop('backup_coverage_report', None),
        'dry_run_summary': request.session.pop('backup_dry_run_summary', None),
        'pretty_bytes': pretty_bytes,
    }
    return render(request, 'dashboard/backups.html', context)

def get_dashboard_stats(request):
    """Get overall dashboard statistics"""
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    avg_progress_raw = CourseProgress.objects.aggregate(avg=Avg('progress'))['avg'] or 0
    selected_course_id = request.GET.get('insight_course', 'all')
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
            'unread': Notification.objects.filter(admin_seen_at__isnull=True).count(),
            'sent_this_week': Notification.objects.filter(sent_at__gte=week_ago).count(),
        },
        'recent_activity': get_recent_activity(),
        'backup_summary': get_backup_summary(),
        'learning_insights': build_learning_insight_data(selected_course_id),
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
