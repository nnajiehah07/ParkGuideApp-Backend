from django.utils import timezone
from rest_framework import serializers

from .models import ARHotspot, ARPanorama, ARQuizQuestion, ARScenario, ARTrainingProgress


class ARHotspotSerializer(serializers.ModelSerializer):
    class Meta:
        model = ARHotspot
        fields = [
            'id', 'hotspot_id', 'title', 'content', 'position_yaw', 'position_pitch',
            'icon_type', 'color_hint', 'order',
        ]


class ARPanoramaSerializer(serializers.ModelSerializer):
    hotspots = ARHotspotSerializer(many=True, read_only=True)

    class Meta:
        model = ARPanorama
        fields = ['id', 'name', 'panorama_url', 'order', 'hotspots']


class ARQuizQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ARQuizQuestion
        fields = [
            'id', 'question_text', 'options', 'correct_option_index',
            'correct_explanation', 'incorrect_explanation', 'order',
        ]


class ARScenarioListSerializer(serializers.ModelSerializer):
    hotspot_count = serializers.SerializerMethodField()
    all_hotspots_count = serializers.SerializerMethodField()
    quiz_count = serializers.IntegerField(source='quizzes.count', read_only=True)
    user_progress = serializers.SerializerMethodField()

    class Meta:
        model = ARScenario
        fields = [
            'id', 'code', 'title', 'description', 'scenario_type', 'difficulty',
            'duration_minutes', 'thumbnail', 'initial_panorama_url',
            'learning_objectives', 'field_brief', 'success_criteria',
            'hotspot_count', 'all_hotspots_count', 'quiz_count', 'user_progress',
        ]

    def get_hotspot_count(self, obj):
        return ARHotspot.objects.filter(panorama__scenario=obj).count()

    def get_all_hotspots_count(self, obj):
        return self.get_hotspot_count(obj)

    def get_user_progress(self, obj):
        user = self.context['request'].user
        if not user.is_authenticated:
            return None
        progress = ARTrainingProgress.objects.filter(user=user, scenario=obj).first()
        if not progress:
            return None
        return {
            'completion_percentage': progress.completion_percentage,
            'best_score': progress.best_score,
            'is_completed': progress.is_completed,
            'updated_at': progress.updated_at,
        }


class ARScenarioDetailSerializer(ARScenarioListSerializer):
    panoramas = ARPanoramaSerializer(many=True, read_only=True)
    quizzes = ARQuizQuestionSerializer(many=True, read_only=True)

    class Meta(ARScenarioListSerializer.Meta):
        fields = ARScenarioListSerializer.Meta.fields + ['panoramas', 'quizzes']


class ARTrainingProgressSerializer(serializers.ModelSerializer):
    scenario_title = serializers.JSONField(source='scenario.title', read_only=True)
    scenario_type = serializers.CharField(source='scenario.scenario_type', read_only=True)

    class Meta:
        model = ARTrainingProgress
        fields = [
            'id', 'scenario', 'scenario_title', 'scenario_type', 'hotspots_discovered',
            'panoramas_visited', 'quizzes_completed', 'completion_percentage',
            'best_score', 'time_spent_seconds', 'is_completed', 'started_at',
            'completed_at', 'updated_at',
        ]
        read_only_fields = ['id', 'scenario_title', 'scenario_type', 'started_at', 'completed_at', 'updated_at']

    def update(self, instance, validated_data):
        quizzes = validated_data.get('quizzes_completed', instance.quizzes_completed)
        scores = [
            float(item.get('score', 0))
            for item in quizzes
            if isinstance(item, dict) and item.get('score') is not None
        ]
        if scores:
            validated_data['best_score'] = max(instance.best_score, max(scores))
        if validated_data.get('is_completed') and not instance.completed_at:
            validated_data['completed_at'] = timezone.now()
            validated_data['completion_percentage'] = 100
        return super().update(instance, validated_data)
