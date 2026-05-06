from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ARHotspotViewSet,
    ARQuizViewSet,
    ARScenarioViewSet,
    ARTrainingProgressViewSet,
    ARTrainingStatisticsViewSet,
)

router = DefaultRouter()
router.register(r'scenarios', ARScenarioViewSet, basename='ar-scenario')
router.register(r'hotspots', ARHotspotViewSet, basename='ar-hotspot')
router.register(r'quiz', ARQuizViewSet, basename='ar-quiz')
router.register(r'progress', ARTrainingProgressViewSet, basename='ar-progress')
router.register(r'statistics', ARTrainingStatisticsViewSet, basename='ar-statistics')

urlpatterns = [
    path('', include(router.urls)),
]
