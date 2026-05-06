from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class ARScenario(models.Model):
    SCENARIO_TYPES = (
        ('biodiversity', 'Biodiversity'),
        ('ecotourism', 'Eco-tourism'),
        ('wildlife', 'Wildlife Encounter'),
        ('guiding', 'Guide Communication'),
    )
    DIFFICULTIES = (
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    )

    code = models.SlugField(max_length=80, unique=True)
    title = models.JSONField()
    description = models.JSONField()
    scenario_type = models.CharField(max_length=20, choices=SCENARIO_TYPES)
    difficulty = models.CharField(max_length=20, choices=DIFFICULTIES, default='intermediate')
    duration_minutes = models.PositiveIntegerField(default=12)
    thumbnail = models.URLField(max_length=700, blank=True)
    initial_panorama_url = models.URLField(max_length=700, blank=True)
    learning_objectives = models.JSONField(default=list, blank=True)
    field_brief = models.JSONField(default=dict, blank=True)
    success_criteria = models.JSONField(default=list, blank=True)
    is_published = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('order', 'code')

    def __str__(self):
        return self.title.get('en', self.code)


class ARPanorama(models.Model):
    scenario = models.ForeignKey(ARScenario, related_name='panoramas', on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    panorama_url = models.URLField(max_length=700)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('scenario', 'order')

    def __str__(self):
        return f'{self.scenario.code} - {self.name}'


class ARHotspot(models.Model):
    panorama = models.ForeignKey(ARPanorama, related_name='hotspots', on_delete=models.CASCADE)
    hotspot_id = models.SlugField(max_length=80)
    title = models.JSONField()
    content = models.JSONField(default=dict)
    position_yaw = models.FloatField(default=0)
    position_pitch = models.FloatField(default=0)
    icon_type = models.CharField(max_length=40, default='map-marker-question')
    color_hint = models.CharField(max_length=20, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('panorama', 'order')
        unique_together = ('panorama', 'hotspot_id')

    def __str__(self):
        return f'{self.panorama.scenario.code} - {self.hotspot_id}'


class ARQuizQuestion(models.Model):
    scenario = models.ForeignKey(ARScenario, related_name='quizzes', on_delete=models.CASCADE)
    question_text = models.JSONField()
    options = models.JSONField(default=dict)
    correct_option_index = models.PositiveSmallIntegerField(default=0)
    correct_explanation = models.JSONField(default=dict, blank=True)
    incorrect_explanation = models.JSONField(default=dict, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('scenario', 'order')

    def __str__(self):
        return f'{self.scenario.code} question {self.order}'


class ARTrainingProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='ar_training_progress', on_delete=models.CASCADE)
    scenario = models.ForeignKey(ARScenario, related_name='progress_records', on_delete=models.CASCADE)
    hotspots_discovered = models.JSONField(default=list, blank=True)
    panoramas_visited = models.JSONField(default=list, blank=True)
    quizzes_completed = models.JSONField(default=list, blank=True)
    completion_percentage = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    best_score = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    time_spent_seconds = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ar_training_artrainingprogress_v2'
        unique_together = ('user', 'scenario')
        ordering = ('-updated_at',)

    def __str__(self):
        return f'{self.user} - {self.scenario.code} ({self.completion_percentage:.0f}%)'
