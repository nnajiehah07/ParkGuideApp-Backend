from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import MonitorEvidenceUploadView, MonitorSessionStartView, MonitorSessionStopView, MonitorStatusView, ViolationAlertViewSet

router = DefaultRouter()
router.register(r"alerts", ViolationAlertViewSet, basename="monitor-alert")

urlpatterns = [
    path("status/", MonitorStatusView.as_view()),
    path("session/start/", MonitorSessionStartView.as_view()),
    path("session/stop/", MonitorSessionStopView.as_view()),
    path("evidence/", MonitorEvidenceUploadView.as_view()),
    path("", include(router.urls)),
]
