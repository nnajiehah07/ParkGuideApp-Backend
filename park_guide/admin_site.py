from django.contrib.admin import AdminSite
from django.db.models import Avg, Count, Sum
from django.urls import reverse
from django.utils import timezone

from accounts.admin import CustomUserAdmin
from accounts.models import CustomUser
from courses.admin import CourseAdmin, ModuleAdmin
from courses.models import Course, Module
from notifications.admin import NotificationAdmin, UserNotificationAdmin
from notifications.models import Notification, UserNotification
from secure_files.admin import SecureFileAdmin
from secure_files.models import SecureFile
from user_progress.admin import (
    BadgeAdmin,
    CourseProgressRecordAdmin,
    ModuleProgressRecordAdmin,
    UserBadgeAdmin,
)
from user_progress.models import Badge, CourseProgressRecord, ModuleProgressRecord, UserBadge


class ParkGuideAdminSite(AdminSite):
    site_header = "Park Guide Admin"
    site_title = "Park Guide Admin"
    index_title = "Operations Dashboard"
    site_url = None
    enable_nav_sidebar = False

    def each_context(self, request):
        context = super().each_context(request)

        total_users = CustomUser.objects.count()
        active_learners = CustomUser.objects.filter(
            is_active=True,
            is_staff=False,
            is_superuser=False,
        ).count()
        app_users_qs = CustomUser.objects.filter(is_staff=False, is_superuser=False)

        course_progress_qs = CourseProgressRecord.objects.select_related("course", "user")
        module_progress_qs = ModuleProgressRecord.objects.select_related("module", "user")
        user_badges_qs = UserBadge.objects.select_related("user", "badge")
        user_notifications_qs = UserNotification.objects.select_related("notification", "user")

        total_courses = Course.objects.count()
        total_modules = Module.objects.count()

        completed_courses = course_progress_qs.filter(completed=True).values("course_id").distinct().count()
        completed_modules = module_progress_qs.filter(completed=True).values("module_id").distinct().count()
        pending_badges = user_badges_qs.filter(status=UserBadge.STATUS_PENDING).count()
        unread_notifications = user_notifications_qs.filter(is_read=False).count()
        total_files = SecureFile.objects.count()
        total_storage_bytes = SecureFile.objects.aggregate(total=Sum("size"))["total"] or 0
        now = timezone.now()
        seven_days_ago = now - timezone.timedelta(days=7)
        fourteen_days_ago = now - timezone.timedelta(days=14)
        in_progress_courses = course_progress_qs.filter(progress__gt=0, completed=False).count()
        not_started_courses = course_progress_qs.filter(progress=0, completed=False).count()
        stalled_courses = course_progress_qs.filter(progress__gt=0, completed=False, updated_at__lt=fourteen_days_ago).count()
        unread_admin_notifications = user_notifications_qs.filter(user=request.user, is_read=False).count()
        new_users_this_week = app_users_qs.filter(date_joined__gte=seven_days_ago).count()

        recent_notifications = Notification.objects.select_related("created_by")[:5]
        recent_uploads = SecureFile.objects.select_related("owner")[:5]
        recent_badges = user_badges_qs.order_by("-awarded_at")[:5]
        recent_learners = app_users_qs.order_by("-date_joined")[:5]

        top_courses = list(
            Course.objects.annotate(module_count=Count("modules"))
            .order_by("-module_count", "id")[:5]
        )

        course_progress_total = course_progress_qs.count()
        course_completion_rate = 0 if total_courses == 0 else round((completed_courses / total_courses) * 100)
        average_course_progress = round((course_progress_qs.aggregate(avg=Avg("progress"))["avg"] or 0) * 100)

        module_progress_total = module_progress_qs.count()
        module_completion_rate = 0 if total_modules == 0 else round((completed_modules / total_modules) * 100)

        badge_granted_total = user_badges_qs.filter(status=UserBadge.STATUS_GRANTED).count()
        badge_rejected_total = user_badges_qs.filter(status=UserBadge.STATUS_REJECTED).count()
        badge_total = user_badges_qs.count()

        notifications_total = user_notifications_qs.count()
        notifications_read = user_notifications_qs.filter(is_read=True).count()
        notifications_read_rate = 0 if notifications_total == 0 else round((notifications_read / notifications_total) * 100)

        app_cards = [
            {
                "title": "Users",
                "value": total_users,
                "subtitle": f"{active_learners} active app users",
                "url": reverse("admin:accounts_customuser_changelist"),
            },
            {
                "title": "Course Completions",
                "value": completed_courses,
                "subtitle": f"{course_completion_rate}% completion rate",
                "url": reverse("admin:user_progress_courseprogressrecord_changelist"),
            },
            {
                "title": "Pending Badges",
                "value": pending_badges,
                "subtitle": f"{badge_granted_total} granted overall",
                "url": reverse("admin:user_progress_userbadge_changelist") + "?status__exact=pending",
            },
            {
                "title": "Unread Notifications",
                "value": unread_notifications,
                "subtitle": f"{notifications_read_rate}% read rate",
                "url": reverse("admin:notifications_usernotification_changelist") + "?is_read__exact=0",
            },
            {
                "title": "Secure Files",
                "value": total_files,
                "subtitle": self._format_bytes(total_storage_bytes),
                "url": reverse("admin:secure_files_securefile_changelist"),
            },
        ]

        management_links = [
            {
                "label": "Manage courses",
                "description": "Create training courses and modules.",
                "url": reverse("admin:courses_course_changelist"),
            },
            {
                "label": "Review learner progress",
                "description": "Check completions, averages, and learners who are still moving through training.",
                "url": reverse("admin:user_progress_courseprogressrecord_changelist"),
            },
            {
                "label": "Approve badges",
                "description": "Handle pending badge awards quickly.",
                "url": reverse("admin:user_progress_userbadge_changelist") + "?status__exact=pending",
            },
            {
                "label": "Send notifications",
                "description": "Broadcast updates to app users.",
                "url": reverse("admin:notifications_notification_changelist"),
            },
            {
                "label": "Manage secure files",
                "description": "Upload and review private training assets.",
                "url": reverse("admin:secure_files_securefile_changelist"),
            },
        ]

        chart_panels = [
            {
                "title": "Course Completion",
                "value": f"{course_completion_rate}%",
                "subtitle": f"{completed_courses} of {total_courses} courses completed",
                "tone": "green",
                "percent": course_completion_rate,
            },
            {
                "title": "Average Course Progress",
                "value": f"{average_course_progress}%",
                "subtitle": "Average completion across all learner course records",
                "tone": "blue",
                "percent": average_course_progress,
            },
            {
                "title": "Module Completion",
                "value": f"{module_completion_rate}%",
                "subtitle": f"{completed_modules} of {total_modules} modules completed",
                "tone": "gold",
                "percent": module_completion_rate,
            },
        ]

        badge_breakdown = [
            {
                "label": "Granted",
                "value": badge_granted_total,
                "percent": 0 if badge_total == 0 else round((badge_granted_total / badge_total) * 100),
                "tone": "green",
            },
            {
                "label": "Pending",
                "value": pending_badges,
                "percent": 0 if badge_total == 0 else round((pending_badges / badge_total) * 100),
                "tone": "gold",
            },
            {
                "label": "Rejected",
                "value": badge_rejected_total,
                "percent": 0 if badge_total == 0 else round((badge_rejected_total / badge_total) * 100),
                "tone": "red",
            },
        ]

        notification_breakdown = [
            {
                "label": "Read",
                "value": notifications_read,
                "percent": 0 if notifications_total == 0 else round((notifications_read / notifications_total) * 100),
                "tone": "green",
            },
            {
                "label": "Unread",
                "value": unread_notifications,
                "percent": 0 if notifications_total == 0 else round((unread_notifications / notifications_total) * 100),
                "tone": "blue",
            },
        ]

        urgent_actions = [
            {
                "label": "Review pending badges",
                "value": pending_badges,
                "detail": "Learners waiting for manual approval",
                "url": reverse("admin:user_progress_userbadge_changelist") + "?status__exact=pending",
                "tone": "gold",
            },
            {
                "label": "Unread admin alerts",
                "value": unread_admin_notifications,
                "detail": "Notifications sent to your staff account",
                "url": reverse("admin:notifications_usernotification_changelist") + f"?user__id__exact={request.user.id}&is_read__exact=0",
                "tone": "blue",
            },
            {
                "label": "Stalled learners",
                "value": stalled_courses,
                "detail": "In-progress course records quiet for 14+ days",
                "url": reverse("admin:user_progress_courseprogressrecord_changelist"),
                "tone": "red",
            },
        ]

        admin_watchlist = [
            {
                "title": "Learners in progress",
                "value": in_progress_courses,
                "subtitle": "Course records with active movement",
                "url": reverse("admin:user_progress_courseprogressrecord_changelist"),
            },
            {
                "title": "Not started",
                "value": not_started_courses,
                "subtitle": "Course records with no progress yet",
                "url": reverse("admin:user_progress_courseprogressrecord_changelist"),
            },
            {
                "title": "New users this week",
                "value": new_users_this_week,
                "subtitle": "Fresh learner accounts in the last 7 days",
                "url": reverse("admin:accounts_customuser_changelist"),
            },
            {
                "title": "Private file storage",
                "value": self._format_bytes(total_storage_bytes).replace(" stored", ""),
                "subtitle": f"{total_files} secure files available",
                "url": reverse("admin:secure_files_securefile_changelist"),
            },
        ]

        quick_routes = [
            {
                "group": "Review",
                "items": [
                    {
                        "label": "Pending badge queue",
                        "meta": f"{pending_badges} waiting",
                        "url": reverse("admin:user_progress_userbadge_changelist") + "?status__exact=pending",
                    },
                    {
                        "label": "Learner course progress",
                        "meta": f"{course_progress_total} records",
                        "url": reverse("admin:user_progress_courseprogressrecord_changelist"),
                    },
                    {
                        "label": "Unread notification delivery",
                        "meta": f"{unread_notifications} unread",
                        "url": reverse("admin:notifications_usernotification_changelist") + "?is_read__exact=0",
                    },
                ],
            },
            {
                "group": "Create",
                "items": [
                    {
                        "label": "Add a course",
                        "meta": "Create new training content",
                        "url": reverse("admin:courses_course_add"),
                    },
                    {
                        "label": "Draft a notification",
                        "meta": "Broadcast an app update",
                        "url": reverse("admin:notifications_notification_add"),
                    },
                    {
                        "label": "Manage secure files",
                        "meta": "Upload or review private assets",
                        "url": reverse("admin:secure_files_securefile_changelist"),
                    },
                ],
            },
        ]

        workspace_sections = [
            {
                "title": "People and Progress",
                "items": [
                    {
                        "label": "Users",
                        "description": "Manage app users, staff access, and learner accounts.",
                        "url": reverse("admin:accounts_customuser_changelist"),
                    },
                    {
                        "label": "Course Progress",
                        "description": "Track learner journey, stalled records, and completions.",
                        "url": reverse("admin:user_progress_courseprogressrecord_changelist"),
                    },
                    {
                        "label": "Module Progress",
                        "description": "Review module-level movement and completion patterns.",
                        "url": reverse("admin:user_progress_moduleprogressrecord_changelist"),
                    },
                ],
            },
            {
                "title": "Learning Content",
                "items": [
                    {
                        "label": "Courses",
                        "description": "Create and organize training courses.",
                        "url": reverse("admin:courses_course_changelist"),
                    },
                    {
                        "label": "Modules",
                        "description": "Manage learning modules and content structure.",
                        "url": reverse("admin:courses_module_changelist"),
                    },
                    {
                        "label": "Secure Files",
                        "description": "Upload and audit private learning assets.",
                        "url": reverse("admin:secure_files_securefile_changelist"),
                    },
                ],
            },
            {
                "title": "Recognition and Outreach",
                "items": [
                    {
                        "label": "Badge Rules",
                        "description": "Configure requirements and badge approval mode.",
                        "url": reverse("admin:user_progress_badge_changelist"),
                    },
                    {
                        "label": "Badge Awards",
                        "description": "Approve pending badges and review award history.",
                        "url": reverse("admin:user_progress_userbadge_changelist"),
                    },
                    {
                        "label": "Notifications",
                        "description": "Send announcements and monitor delivery entries.",
                        "url": reverse("admin:notifications_notification_changelist"),
                    },
                ],
            },
        ]

        top_course_insights = []
        for course in top_courses:
            records = course_progress_qs.filter(course=course)
            learners = records.count()
            completions = records.filter(completed=True).count()
            completion_rate = 0 if learners == 0 else round((completions / learners) * 100)
            avg_progress = round((records.aggregate(avg=Avg("progress"))["avg"] or 0) * 100)
            top_course_insights.append(
                {
                    "course": course,
                    "module_count": course.module_count,
                    "learners": learners,
                    "completion_rate": completion_rate,
                    "avg_progress": avg_progress,
                }
            )

        context.update(
            {
                "dashboard_cards": app_cards,
                "dashboard_links": management_links,
                "dashboard_chart_panels": chart_panels,
                "urgent_actions": urgent_actions,
                "admin_watchlist": admin_watchlist,
                "quick_routes": quick_routes,
                "workspace_sections": workspace_sections,
                "badge_breakdown": badge_breakdown,
                "notification_breakdown": notification_breakdown,
                "recent_notifications": recent_notifications,
                "recent_uploads": recent_uploads,
                "recent_badges": recent_badges,
                "recent_learners": recent_learners,
                "top_courses": top_courses,
                "top_course_insights": top_course_insights,
                "user_snapshot": {
                    "staff_users": CustomUser.objects.filter(is_staff=True).count(),
                    "inactive_users": CustomUser.objects.filter(is_active=False).count(),
                    "new_this_week": new_users_this_week,
                },
                "content_snapshot": {
                    "courses": total_courses,
                    "modules": total_modules,
                    "badges": Badge.objects.filter(is_active=True).count(),
                    "notifications": Notification.objects.count(),
                },
            }
        )
        return context

    @staticmethod
    def _format_bytes(size):
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit} stored"
                return f"{value:.1f} {unit} stored"
            value /= 1024
        return "0 B stored"


admin_site = ParkGuideAdminSite(name="admin")

admin_site.register(CustomUser, CustomUserAdmin)
admin_site.register(Course, CourseAdmin)
admin_site.register(Module, ModuleAdmin)
admin_site.register(ModuleProgressRecord, ModuleProgressRecordAdmin)
admin_site.register(CourseProgressRecord, CourseProgressRecordAdmin)
admin_site.register(Badge, BadgeAdmin)
admin_site.register(UserBadge, UserBadgeAdmin)
admin_site.register(Notification, NotificationAdmin)
admin_site.register(UserNotification, UserNotificationAdmin)
admin_site.register(SecureFile, SecureFileAdmin)
