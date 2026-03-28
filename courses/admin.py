from django.contrib import admin
from django.utils.html import format_html

from courses.models import CourseProgress, ModuleProgress
from park_guide.admin_mixins import DashboardStatsChangeListMixin

from .models import Course, Module


class ModuleInline(admin.TabularInline):
    model = Module
    extra = 0
    fields = ('title', 'content', 'quiz')
    readonly_fields = ()
    show_change_link = True


@admin.register(Course)
class CourseAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = ('id', 'title_en', 'module_count', 'learner_coverage', 'completion_snapshot')
    search_fields = ('title',)
    inlines = [ModuleInline]
    dashboard_title = 'Training Catalogue'
    dashboard_description = 'See which courses are richest in content and how far learners are getting through them.'

    def title_en(self, obj):
        return obj.title.get('en', 'Untitled')
    title_en.short_description = "Title"

    def module_count(self, obj):
        return obj.modules.count()
    module_count.short_description = 'Modules'

    def learner_coverage(self, obj):
        learners = CourseProgress.objects.filter(course=obj).count()
        return format_html('<strong>{}</strong><br><span class="admin-subtle">learners enrolled</span>', learners)
    learner_coverage.short_description = 'Learners'

    def completion_snapshot(self, obj):
        records = CourseProgress.objects.filter(course=obj)
        total = records.count()
        completed = records.filter(completed=True).count()
        percent = 0 if total == 0 else (completed / total) * 100
        return self.render_progress_bar(percent, f'{completed}/{total} completed', tone='green')
    completion_snapshot.short_description = 'Completion'

    def get_dashboard_stats(self, request, queryset):
        module_total = sum(course.modules.count() for course in queryset)
        learners = CourseProgress.objects.filter(course__in=queryset).count()
        completions = CourseProgress.objects.filter(course__in=queryset, completed=True).count()
        percent = 0 if learners == 0 else round((completions / learners) * 100)
        return [
            {'label': 'Courses', 'value': queryset.count()},
            {'label': 'Modules in view', 'value': module_total},
            {'label': 'Learner records', 'value': learners},
            {'label': 'Completed courses', 'value': completions},
            {'label': 'Completion rate', 'value': f'{percent}%'},
        ]


@admin.register(Module)
class ModuleAdmin(DashboardStatsChangeListMixin, admin.ModelAdmin):
    list_display = ('id', 'course', 'title_en', 'has_quiz', 'completion_snapshot')
    list_filter = ('course',)
    search_fields = ('title',)
    dashboard_title = 'Modules'
    dashboard_description = 'Review learning units, quiz coverage, and completion health at module level.'

    def title_en(self, obj):
        return obj.title.get('en', 'Untitled')
    title_en.short_description = "Title"

    def has_quiz(self, obj):
        return bool(obj.quiz)
    has_quiz.boolean = True
    has_quiz.short_description = 'Quiz'

    def completion_snapshot(self, obj):
        records = ModuleProgress.objects.filter(module=obj)
        total = records.count()
        completed = records.filter(completed=True).count()
        percent = 0 if total == 0 else (completed / total) * 100
        return self.render_progress_bar(percent, f'{completed}/{total} complete', tone='blue')
    completion_snapshot.short_description = 'Completion'

    def get_dashboard_stats(self, request, queryset):
        with_quiz = queryset.exclude(quiz__isnull=True).exclude(quiz={}).count()
        total = queryset.count()
        module_records = ModuleProgress.objects.filter(module__in=queryset)
        module_completions = module_records.filter(completed=True).count()
        return [
            {'label': 'Modules', 'value': total},
            {'label': 'With quiz', 'value': with_quiz},
            {'label': 'Without quiz', 'value': total - with_quiz},
            {'label': 'Completions', 'value': module_completions},
        ]
