from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Q, Avg, Sum
from django.utils import timezone
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from datetime import timedelta
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
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

from accounts.models import CustomUser
from courses.models import Course, Module, ModuleProgress, CourseProgress
from user_progress.models import Badge, UserBadge
from notifications.models import Notification, UserNotification
from secure_files.models import SecureFile
from secure_files.services.firebase_storage import delete_file as delete_secure_blob, upload_file
from .models import BackupSetting, BackupHistory, BackupAuditLog


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
    if request.method == 'GET' and request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        action = request.GET.get('action')
        if action == 'badge_details':
            badge_id = request.GET.get('badge_id')
            badge = Badge.objects.filter(id=badge_id).first()
            if not badge:
                return JsonResponse({'ok': False, 'error': 'Badge not found.'}, status=404)
            badge_users = (
                UserBadge.objects
                .filter(badge=badge, status__in=['granted', 'pending'])
                .select_related('user', 'awarded_by')
                .order_by('status', 'user__username')
            )
            granted_users = []
            pending_users = []
            for item in badge_users:
                user_data = {
                    'username': item.user.username,
                    'email': item.user.email,
                    'status': item.status,
                    'awarded_at': item.awarded_at.strftime('%b %d, %Y %H:%M') if item.awarded_at else '',
                    'awarded_by': item.awarded_by.username if item.awarded_by else '',
                }
                if item.status == 'granted':
                    granted_users.append(user_data)
                elif item.status == 'pending':
                    pending_users.append(user_data)
            return JsonResponse({
                'ok': True,
                'badge': {
                    'id': badge.id,
                    'name': badge.name,
                    'description': badge.description or '',
                    'granted_count': len(granted_users),
                    'pending_count': len(pending_users),
                    'granted_users': granted_users,
                    'pending_users': pending_users,
                }
            })
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
            is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            with transaction.atomic():
                user_badge = (
                    UserBadge.objects
                    .select_for_update()
                    .select_related('user', 'badge')
                    .filter(id=user_badge_id)
                    .first()
                )
                if not user_badge:
                    if is_ajax:
                        return JsonResponse({'ok': False, 'error': 'Badge request not found.'}, status=404)
                    messages.error(request, 'Badge request not found.')
                    return redirect('dashboard:badges')
                if user_badge.status != 'pending':
                    message = f'This badge request was already processed as {user_badge.status}.'
                    if is_ajax:
                        return JsonResponse({
                            'ok': False,
                            'error': message,
                            'already_processed': True,
                            'current_status': user_badge.status,
                            'user_badge_id': user_badge.id,
                            'badge_id': user_badge.badge_id,
                            'pending_total': UserBadge.objects.filter(status='pending').count(),
                        }, status=409)
                    messages.warning(request, message)
                    return redirect('dashboard:badges')
                if action == 'approve_badge':
                    user_badge.status = 'granted'
                    user_badge.is_awarded = True
                    user_badge.awarded_by = request.user
                    user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by'])
                    message_text = f'Approved {user_badge.badge.name} for {user_badge.user.username}.'
                else:
                    user_badge.status = 'rejected'
                    user_badge.is_awarded = False
                    user_badge.awarded_by = None
                    user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by'])
                    message_text = f'Rejected {user_badge.badge.name} for {user_badge.user.username}.'
            if is_ajax:
                badge_counts = Badge.objects.annotate(
                    pending_approvals=Count('user_badges', filter=Q(user_badges__status='pending')),
                    granted_badges=Count('user_badges', filter=Q(user_badges__status='granted')),
                ).filter(id=user_badge.badge_id).values('pending_approvals', 'granted_badges').first()
                return JsonResponse({
                    'ok': True,
                    'message': message_text,
                    'user_badge_id': user_badge.id,
                    'badge_id': user_badge.badge_id,
                    'pending_total': UserBadge.objects.filter(status='pending').count(),
                    'badge_pending': badge_counts['pending_approvals'] if badge_counts else 0,
                    'badge_granted': badge_counts['granted_badges'] if badge_counts else 0,
                })
            messages.success(request, message_text)
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
