from django.db.models import Sum
from django.utils.html import format_html


class DashboardStatsChangeListMixin:
    dashboard_title = None
    dashboard_description = None

    def get_dashboard_title(self, request):
        return self.dashboard_title or self.model._meta.verbose_name_plural.title()

    def get_dashboard_description(self, request):
        return self.dashboard_description

    def get_dashboard_stats(self, request, queryset):
        return []

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        response = super().changelist_view(request, extra_context=extra_context)
        try:
            queryset = response.context_data["cl"].queryset
        except (AttributeError, KeyError, TypeError):
            return response

        response.context_data["dashboard_title"] = self.get_dashboard_title(request)
        response.context_data["dashboard_description"] = self.get_dashboard_description(request)
        response.context_data["dashboard_stats"] = self.get_dashboard_stats(request, queryset)
        return response

    @staticmethod
    def format_bytes(size):
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size or 0)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return "0 B"

    @staticmethod
    def sum_bytes(queryset, field_name="size"):
        return queryset.aggregate(total=Sum(field_name))["total"] or 0

    @staticmethod
    def render_progress_bar(value, label=None, tone="green"):
        percentage = max(0, min(100, int(round(value or 0))))
        label = label or f"{percentage}%"
        return format_html(
            """
            <div class="modern-progress modern-progress-{tone}">
                <div class="progress-track">
                    <div class="progress-fill fill-{tone}" style="width: {percentage}%;"></div>
                </div>
                <span class="progress-label">{label}</span>
            </div>
            """,
            tone=tone,
            percentage=percentage,
            label=label,
        )

    @staticmethod
    def render_status_pill(label, tone="neutral"):
        return format_html(
            '<span class="admin-status admin-status-{tone}">{label}</span>',
            tone=tone,
            label=label,
        )
