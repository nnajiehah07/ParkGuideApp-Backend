from django.db.models import Avg, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ARHotspot, ARQuizQuestion, ARScenario, ARTrainingProgress
from .seed_data import ensure_seed_data
from .serializers import (
    ARScenarioDetailSerializer,
    ARScenarioListSerializer,
    ARTrainingProgressSerializer,
)


class ARScenarioViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not ARScenario.objects.exists():
            ensure_seed_data()

        queryset = ARScenario.objects.filter(is_published=True).prefetch_related('panoramas__hotspots', 'quizzes')
        scenario_type = self.request.query_params.get('type')
        if scenario_type:
            queryset = queryset.filter(scenario_type=scenario_type)
        return queryset

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ARScenarioDetailSerializer
        return ARScenarioListSerializer

    @action(detail=True, methods=['get'])
    def start(self, request, pk=None):
        scenario = self.get_object()
        progress, _created = ARTrainingProgress.objects.get_or_create(
            user=request.user,
            scenario=scenario,
            defaults={'started_at': timezone.now()},
        )
        first_panorama = scenario.panoramas.first()
        if first_panorama and first_panorama.id not in progress.panoramas_visited:
            progress.panoramas_visited = [*progress.panoramas_visited, first_panorama.id]
            progress.save(update_fields=['panoramas_visited', 'updated_at'])

        return Response({
            'scenario_id': scenario.id,
            'progress_id': progress.id,
            'message': 'VR/AR training session started.',
            'field_brief': scenario.field_brief,
            'success_criteria': scenario.success_criteria,
        })


class ARHotspotViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'])
    def discover(self, request, pk=None):
        hotspot = get_object_or_404(ARHotspot.objects.select_related('panorama__scenario'), pk=pk)
        progress, _created = ARTrainingProgress.objects.get_or_create(
            user=request.user,
            scenario=hotspot.panorama.scenario,
        )
        hotspot_key = hotspot.hotspot_id
        panorama_id = hotspot.panorama_id
        if hotspot_key not in progress.hotspots_discovered:
            progress.hotspots_discovered = [*progress.hotspots_discovered, hotspot_key]
        if panorama_id not in progress.panoramas_visited:
            progress.panoramas_visited = [*progress.panoramas_visited, panorama_id]

        total_hotspots = ARHotspot.objects.filter(panorama__scenario=hotspot.panorama.scenario).count()
        hotspot_pct = (len(progress.hotspots_discovered) / total_hotspots * 70) if total_hotspots else 70
        progress.completion_percentage = max(progress.completion_percentage, min(95, hotspot_pct))
        progress.save()
        return Response({
            'discovered': True,
            'hotspots_discovered': progress.hotspots_discovered,
            'completion_percentage': progress.completion_percentage,
        })


class ARQuizViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'])
    def answer(self, request, pk=None):
        quiz = get_object_or_404(ARQuizQuestion.objects.select_related('scenario'), pk=pk)
        answer_index = int(request.data.get('answer_index', -1))
        correct = answer_index == quiz.correct_option_index
        progress, _created = ARTrainingProgress.objects.get_or_create(
            user=request.user,
            scenario=quiz.scenario,
        )

        attempt = {
            'quiz_id': quiz.id,
            'answer_index': answer_index,
            'correct': correct,
            'score': 100 if correct else 0,
            'time_taken_seconds': int(request.data.get('time_taken_seconds') or 0),
            'answered_at': timezone.now().isoformat(),
        }
        progress.quizzes_completed = [
            item for item in progress.quizzes_completed
            if not isinstance(item, dict) or item.get('quiz_id') != quiz.id
        ] + [attempt]
        progress.best_score = max(progress.best_score, attempt['score'])
        progress.completion_percentage = max(progress.completion_percentage, 85 if correct else 60)
        progress.save()

        return Response({
            'correct': correct,
            'correct_option_index': quiz.correct_option_index,
            'correct_explanation': quiz.correct_explanation,
            'incorrect_explanation': quiz.incorrect_explanation,
            'score': attempt['score'],
        })


class ARTrainingProgressViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ARTrainingProgressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ARTrainingProgress.objects.filter(user=self.request.user).select_related('scenario')

    @action(detail=True, methods=['post'])
    def update_progress(self, request, pk=None):
        progress = self.get_object()
        serializer = self.get_serializer(progress, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ARTrainingStatisticsViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def my_stats(self, request):
        records = ARTrainingProgress.objects.filter(user=request.user)
        aggregate = records.aggregate(
            avg_score=Avg('best_score'),
            total_time=Sum('time_spent_seconds'),
        )
        completed_count = records.filter(is_completed=True).count()
        total_hotspots = sum(len(item.hotspots_discovered or []) for item in records)
        avg_score = aggregate.get('avg_score') or 0
        return Response({
            'scenarios_completed': completed_count,
            'total_hotspots_discovered': total_hotspots,
            'average_quiz_score': round(avg_score, 1),
            'trainingHours': round((aggregate.get('total_time') or 0) / 3600, 1),
            'badges_earned': 1 if completed_count >= 3 else 0,
        })
