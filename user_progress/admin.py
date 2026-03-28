from django.contrib import admin
from django.contrib import messages
from django.db.models import Avg
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from park_guide.admin_mixins import DashboardStatsChangeListMixin

from .models import Badge, CourseProgressRecord, ModuleProgressRecord, UserBadge
from .services import (
    auto_approve_pending_badges,
    auto_reject_pending_badges,
    notify_badge_granted_to_user,
    revoke_badge_from_ineligible_users,
    sync_pending_badges_for_eligible_users,
)


@admin.register(ModuleProgressRecord)
class ModuleProgressRecordAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = ('id', 'user', 'module', 'completion_badge', 'completed_at')
    list_filter = ('completed', 'completed_at', 'module__course')
    search_fields = ('user__email', 'user__username', 'module__title')
    autocomplete_fields = ('user', 'module')
    ordering = ('-completed_at',)
    list_select_related = ('user', 'module', 'module__course')
    dashboard_title = 'Module Progress'
    dashboard_description = 'Track module-level movement with clearer completion states for every learner record.'

    def completion_badge(self, obj):
        return self.render_status_pill('Completed' if obj.completed else 'In progress', 'green' if obj.completed else 'blue')
    completion_badge.short_description = 'State'

    def get_dashboard_stats(self, request, queryset):
        total = queryset.count()
        completed = queryset.filter(completed=True).count()
        percent = 0 if total == 0 else round((completed / total) * 100)
        return [
            {'label': 'Records', 'value': total},
            {'label': 'Completed', 'value': completed},
            {'label': 'Open', 'value': total - completed},
            {'label': 'Completion rate', 'value': f'{percent}%'},
        ]


@admin.register(CourseProgressRecord)
class CourseProgressRecordAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = (
        'learner_snapshot',
        'course_snapshot',
        'progress_overview',
        'journey_story',
        'badge_story',
        'completion_state',
        'updated_at',
    )
    list_filter = ('completed', 'updated_at', 'course')
    search_fields = ('user__email', 'user__username', 'course__title')
    autocomplete_fields = ('user', 'course')
    ordering = ('-updated_at',)
    list_select_related = ('user', 'course')
    dashboard_title = 'Course Progress'
    dashboard_description = 'See learner progress as real completion percentages, not just raw fields.'

    def learner_snapshot(self, obj):
        display_name = obj.user.get_full_name().strip() or obj.user.username or obj.user.email
        return format_html(
            '<div><strong>{}</strong><br><span class="admin-subtle">{}</span></div>',
            display_name,
            obj.user.email,
        )
    learner_snapshot.short_description = 'Learner'
    learner_snapshot.admin_order_field = 'user__email'

    def course_snapshot(self, obj):
        course_title = obj.course.title.get('en', 'Untitled Course')
        total_modules = obj.total_modules or obj.course.modules.count()
        module_label = 'module' if total_modules == 1 else 'modules'
        return format_html(
            '<div><strong>{}</strong><br><span class="admin-subtle">{} {}</span></div>',
            course_title,
            total_modules,
            module_label,
        )
    course_snapshot.short_description = 'Course'
    course_snapshot.admin_order_field = 'course'

    def progress_overview(self, obj):
        percentage = (obj.progress or 0) * 100
        completed_modules = obj.completed_modules or 0
        total_modules = obj.total_modules or 0

        if obj.completed:
            summary = f'Completed all {total_modules} modules'
        elif completed_modules > 0:
            summary = f'{completed_modules} of {total_modules} modules completed'
        else:
            summary = f'Not started yet • 0 of {total_modules} modules completed'

        return format_html(
            '<div><div class="admin-progress-copy"><strong>{}</strong><span class="admin-subtle">{}</span></div>{}</div>',
            f'{percentage:.0f}% complete',
            summary,
            self.render_progress_bar(percentage, f'{completed_modules}/{total_modules} modules', tone='green' if obj.completed else 'blue'),
        )
    progress_overview.short_description = 'Current progress'

    def journey_story(self, obj):
        completed_modules = obj.completed_modules or 0
        total_modules = obj.total_modules or 0
        remaining_modules = max(total_modules - completed_modules, 0)
        latest_completion = (
            obj.user.moduleprogress_set
            .filter(module__course=obj.course, completed=True)
            .select_related('module')
            .order_by('-completed_at')
            .first()
        )

        rows = []
        if obj.completed:
            rows.append(('Course state', 'Learner has finished this course.'))
        elif completed_modules > 0:
            rows.append(('Course state', f'Actively progressing with {remaining_modules} modules remaining.'))
        else:
            rows.append(('Course state', 'Enrolled but no completed modules yet.'))

        if latest_completion:
            module_title = latest_completion.module.title.get('en', 'Untitled Module')
            rows.append(('Last completed', f'{module_title} on {latest_completion.completed_at.strftime("%b %d, %Y")}'))
        else:
            rows.append(('Last completed', 'No module completion recorded yet.'))

        if not obj.completed and total_modules > 0:
            rows.append(('Next milestone', f'Complete {remaining_modules} more module{"s" if remaining_modules != 1 else ""} to finish the course.'))

        return format_html(
            '<div class="admin-story">{}</div>',
            format_html_join(
                '',
                '<div class="admin-story-row"><strong>{}</strong><span class="admin-subtle">{}</span></div>',
                rows,
            ),
        )
    journey_story.short_description = 'Journey details'

    def badge_story(self, obj):
        badges = list(obj.course.progress_badges.filter(is_active=True).order_by('required_completed_modules', 'id'))
        if not badges:
            return format_html(
                '<div class="admin-story"><div class="admin-story-row"><strong>Badge status</strong><span class="admin-subtle">No active course badge linked.</span></div></div>'
            )

        badge_rows = []
        for badge in badges[:2]:
            user_badge = obj.user.badge_progress.filter(badge=badge).first()
            status = user_badge.status if user_badge else UserBadge.STATUS_IN_PROGRESS
            tone = {
                UserBadge.STATUS_GRANTED: 'green',
                UserBadge.STATUS_PENDING: 'gold',
                UserBadge.STATUS_REJECTED: 'red',
                UserBadge.STATUS_IN_PROGRESS: 'blue',
            }.get(status, 'neutral')
            requirement = f'Requires {badge.required_completed_modules} module{"s" if badge.required_completed_modules != 1 else ""}'
            badge_rows.append(
                format_html(
                    '<div class="admin-badge-row"><strong>{}</strong>{}<span class="admin-subtle">{}</span></div>',
                    badge.name,
                    self.render_status_pill(dict(UserBadge.STATUS_CHOICES).get(status, 'Unknown'), tone),
                    requirement,
                )
            )

        extra_badges = len(badges) - 2
        extra_copy = ''
        if extra_badges > 0:
            extra_copy = format_html('<div class="admin-subtle">+{} more course badge rule{}</div>', extra_badges, 's' if extra_badges != 1 else '')

        return format_html(
            '<div class="admin-story">{}{}</div>',
            format_html_join('', '{}', ((row,) for row in badge_rows)),
            extra_copy,
        )
    badge_story.short_description = 'Badge status'

    def completion_state(self, obj):
        if obj.completed:
            return self.render_status_pill('Completed', 'green')
        if (obj.progress or 0) > 0:
            return self.render_status_pill('In progress', 'blue')
        return self.render_status_pill('Not started', 'neutral')
    completion_state.short_description = 'Status'

    def get_dashboard_stats(self, request, queryset):
        total = queryset.count()
        completed = queryset.filter(completed=True).count()
        in_progress = queryset.filter(progress__gt=0, completed=False).count()
        average_progress = queryset.aggregate(avg=Avg('progress'))['avg'] or 0
        return [
            {'label': 'Records', 'value': total},
            {'label': 'Completed', 'value': completed},
            {'label': 'In progress', 'value': in_progress},
            {'label': 'Not started', 'value': max(total - completed - in_progress, 0)},
            {'label': 'Avg progress', 'value': f'{average_progress * 100:.0f}%'},
        ]


@admin.register(Badge)
class BadgeAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = (
        'id',
        'name',
        'course',
        'required_completed_modules',
        'approval_mode',
        'is_active',
        'award_snapshot',
    )
    list_filter = ('is_active', 'course')
    search_fields = ('name', 'description', 'course__title')
    ordering = ('course', 'required_completed_modules', 'name')
    autocomplete_fields = ('course',)
    dashboard_title = 'Badge Rules'
    dashboard_description = 'Configure requirements and see how each badge is performing in the award pipeline.'
    actions = (
        'sync_then_auto_approve_for_selected_badges',
        'sync_pending_for_eligible_users',
        'auto_approve_pending_for_selected_badges',
        'auto_reject_pending_for_selected_badges',
        'revoke_from_ineligible_users',
    )

    def approval_mode(self, obj):
        return self.render_status_pill(
            'Auto approve' if obj.auto_approve_when_eligible else 'Manual review',
            'green' if obj.auto_approve_when_eligible else 'gold',
        )
    approval_mode.short_description = 'Approval mode'

    def award_snapshot(self, obj):
        total = obj.user_badges.count()
        granted = obj.user_badges.filter(status=UserBadge.STATUS_GRANTED).count()
        percent = 0 if total == 0 else (granted / total) * 100
        return self.render_progress_bar(percent, f'{granted}/{total} granted', tone='green')
    award_snapshot.short_description = 'Awards'

    @admin.action(description='Sync pending then auto approve eligible users')
    def sync_then_auto_approve_for_selected_badges(self, request, queryset):
        created_pending_total = 0
        moved_to_pending_total = 0
        auto_granted_during_sync_total = 0
        approved_after_sync_total = 0

        for badge in queryset:
            created_pending, moved_to_pending, auto_granted_during_sync = sync_pending_badges_for_eligible_users(
                badge,
                admin_user=request.user,
            )
            approved_after_sync = auto_approve_pending_badges(badge, admin_user=request.user)

            created_pending_total += created_pending
            moved_to_pending_total += moved_to_pending
            auto_granted_during_sync_total += auto_granted_during_sync
            approved_after_sync_total += approved_after_sync

        self.message_user(
            request,
            (
                f'Sync+approve complete. Pending created: {created_pending_total}, '
                f'moved to pending: {moved_to_pending_total}, '
                f'auto-granted during sync: {auto_granted_during_sync_total}, '
                f'approved after sync: {approved_after_sync_total}.'
            ),
        )

    @admin.action(description='Sync pending badges for eligible users')
    def sync_pending_for_eligible_users(self, request, queryset):
        created_pending_total = 0
        moved_to_pending_total = 0
        auto_granted_total = 0

        for badge in queryset:
            created_pending, moved_to_pending, auto_granted = sync_pending_badges_for_eligible_users(
                badge,
                admin_user=request.user,
            )
            created_pending_total += created_pending
            moved_to_pending_total += moved_to_pending
            auto_granted_total += auto_granted

        self.message_user(
            request,
            (
                f'Pending created: {created_pending_total}, '
                f'moved to pending: {moved_to_pending_total}, '
                f'auto-granted: {auto_granted_total}.'
            ),
        )

    @admin.action(description='Auto approve pending users for selected badges')
    def auto_approve_pending_for_selected_badges(self, request, queryset):
        approved_total = 0
        for badge in queryset:
            approved_total += auto_approve_pending_badges(badge, admin_user=request.user)

        self.message_user(request, f'Approved {approved_total} pending badge records.')

    @admin.action(description='Auto reject pending users for selected badges')
    def auto_reject_pending_for_selected_badges(self, request, queryset):
        rejected_total = 0
        for badge in queryset:
            rejected_total += auto_reject_pending_badges(badge, admin_user=request.user)

        self.message_user(request, f'Rejected {rejected_total} pending badge records.')

    @admin.action(description='Revoke selected badges from ineligible users')
    def revoke_from_ineligible_users(self, request, queryset):
        revoked_total = 0

        for badge in queryset:
            revoked_total += revoke_badge_from_ineligible_users(badge, admin_user=request.user)

        self.message_user(request, f'Revoked {revoked_total} badge records from ineligible users.')

    def get_dashboard_stats(self, request, queryset):
        total_badges = queryset.count()
        granted = UserBadge.objects.filter(badge__in=queryset, status=UserBadge.STATUS_GRANTED).count()
        pending = UserBadge.objects.filter(badge__in=queryset, status=UserBadge.STATUS_PENDING).count()
        return [
            {'label': 'Badges', 'value': total_badges},
            {'label': 'Active', 'value': queryset.filter(is_active=True).count()},
            {'label': 'Granted awards', 'value': granted},
            {'label': 'Pending reviews', 'value': pending},
        ]


@admin.register(UserBadge)
class UserBadgeAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = ('id', 'user', 'badge', 'status_badge', 'awarded_visual', 'timeline', 'revoked_at')
    list_display_links = ('id', 'user', 'badge')
    list_filter = ('status', 'is_awarded', 'badge', 'awarded_at', 'revoked_at')
    search_fields = ('user__email', 'user__username', 'badge__name')
    autocomplete_fields = ('user', 'badge', 'awarded_by', 'revoked_by')
    ordering = ('-awarded_at',)
    list_select_related = ('user', 'badge')
    actions = ('approve_selected_badges', 'reject_selected_badges', 'move_selected_to_in_progress')
    dashboard_title = 'Badge Awards'
    dashboard_description = 'Review pending approvals, granted awards, and rejected badge applications with clearer statuses.'
    readonly_fields = ('awarded_at', 'revoked_at')
    fields = ('user', 'badge', 'status', 'is_awarded', 'awarded_by', 'awarded_at', 'revoked_by', 'revoked_at')

    def status_badge(self, obj):
        tone = {
            UserBadge.STATUS_PENDING: 'gold',
            UserBadge.STATUS_GRANTED: 'green',
            UserBadge.STATUS_REJECTED: 'red',
        }.get(obj.status, 'neutral')
        return self.render_status_pill(obj.get_status_display(), tone)
    status_badge.short_description = 'Status'

    def awarded_visual(self, obj):
        value = 100 if obj.status == UserBadge.STATUS_GRANTED else 50 if obj.status == UserBadge.STATUS_PENDING else 0
        label = 'Awarded' if obj.status == UserBadge.STATUS_GRANTED else 'Pending' if obj.status == UserBadge.STATUS_PENDING else 'Rejected'
        tone = 'green' if obj.status == UserBadge.STATUS_GRANTED else 'gold' if obj.status == UserBadge.STATUS_PENDING else 'red'
        return self.render_progress_bar(value, label, tone=tone)
    awarded_visual.short_description = 'Outcome'

    def timeline(self, obj):
        return format_html(
            '<strong>{}</strong><br><span class="admin-subtle">last changed</span>',
            obj.awarded_at.strftime('%b %d, %Y'),
        )
    timeline.short_description = 'Timeline'

    @admin.action(description='Approve selected pending badge records')
    def approve_selected_badges(self, request, queryset):
        pending_badges = queryset.filter(status=UserBadge.STATUS_PENDING).select_related('badge')
        if not pending_badges.exists():
            self.message_user(request, 'No pending badge records selected.', level=messages.WARNING)
            return

        approved_total = 0
        for user_badge in pending_badges:
            user_badge.status = UserBadge.STATUS_GRANTED
            user_badge.is_awarded = True
            user_badge.awarded_by = request.user
            user_badge.revoked_at = None
            user_badge.revoked_by = None
            user_badge.save(update_fields=['status', 'is_awarded', 'awarded_by', 'revoked_at', 'revoked_by'])
            notify_badge_granted_to_user(user_badge, admin_user=request.user)
            approved_total += 1

        self.message_user(request, f'Approved {approved_total} pending badge records.', level=messages.SUCCESS)

    @admin.action(description='Reject selected pending badge records')
    def reject_selected_badges(self, request, queryset):
        pending_badges = queryset.filter(status=UserBadge.STATUS_PENDING)
        updated = pending_badges.update(
            status=UserBadge.STATUS_REJECTED,
            is_awarded=False,
            revoked_by=request.user,
            revoked_at=timezone.now(),
        )
        if not updated:
            self.message_user(request, 'No pending badge records selected.', level=messages.WARNING)
            return
        self.message_user(request, f'Rejected {updated} pending badge records.', level=messages.SUCCESS)

    @admin.action(description='Move selected badge records back to in progress')
    def move_selected_to_in_progress(self, request, queryset):
        updated = queryset.update(
            status=UserBadge.STATUS_IN_PROGRESS,
            is_awarded=False,
            awarded_by=None,
            revoked_by=None,
            revoked_at=None,
        )
        self.message_user(request, f'Moved {updated} badge records to in progress.', level=messages.SUCCESS)

    def save_model(self, request, obj, form, change):
        previous_status = None
        if change:
            previous_status = UserBadge.objects.filter(pk=obj.pk).values_list('status', flat=True).first()

        super().save_model(request, obj, form, change)

        if obj.status == UserBadge.STATUS_GRANTED and previous_status != UserBadge.STATUS_GRANTED:
            notify_badge_granted_to_user(obj, admin_user=request.user)

    def get_dashboard_stats(self, request, queryset):
        return [
            {'label': 'Records', 'value': queryset.count()},
            {'label': 'Pending', 'value': queryset.filter(status=UserBadge.STATUS_PENDING).count()},
            {'label': 'Granted', 'value': queryset.filter(status=UserBadge.STATUS_GRANTED).count()},
            {'label': 'Rejected', 'value': queryset.filter(status=UserBadge.STATUS_REJECTED).count()},
        ]
