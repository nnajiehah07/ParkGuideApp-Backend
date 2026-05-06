from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ARScenario',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=80, unique=True)),
                ('title', models.JSONField()),
                ('description', models.JSONField()),
                ('scenario_type', models.CharField(choices=[('biodiversity', 'Biodiversity'), ('ecotourism', 'Eco-tourism'), ('wildlife', 'Wildlife Encounter'), ('guiding', 'Guide Communication')], max_length=20)),
                ('difficulty', models.CharField(choices=[('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced')], default='intermediate', max_length=20)),
                ('duration_minutes', models.PositiveIntegerField(default=12)),
                ('thumbnail', models.URLField(blank=True, max_length=700)),
                ('initial_panorama_url', models.URLField(blank=True, max_length=700)),
                ('learning_objectives', models.JSONField(blank=True, default=list)),
                ('field_brief', models.JSONField(blank=True, default=dict)),
                ('success_criteria', models.JSONField(blank=True, default=list)),
                ('is_published', models.BooleanField(default=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ('order', 'code'),
            },
        ),
        migrations.CreateModel(
            name='ARPanorama',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('panorama_url', models.URLField(max_length=700)),
                ('order', models.PositiveIntegerField(default=0)),
                ('scenario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='panoramas', to='ar_training.arscenario')),
            ],
            options={
                'ordering': ('scenario', 'order'),
            },
        ),
        migrations.CreateModel(
            name='ARQuizQuestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question_text', models.JSONField()),
                ('options', models.JSONField(default=dict)),
                ('correct_option_index', models.PositiveSmallIntegerField(default=0)),
                ('correct_explanation', models.JSONField(blank=True, default=dict)),
                ('incorrect_explanation', models.JSONField(blank=True, default=dict)),
                ('order', models.PositiveIntegerField(default=0)),
                ('scenario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='quizzes', to='ar_training.arscenario')),
            ],
            options={
                'ordering': ('scenario', 'order'),
            },
        ),
        migrations.CreateModel(
            name='ARTrainingProgress',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hotspots_discovered', models.JSONField(blank=True, default=list)),
                ('panoramas_visited', models.JSONField(blank=True, default=list)),
                ('quizzes_completed', models.JSONField(blank=True, default=list)),
                ('completion_percentage', models.FloatField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('best_score', models.FloatField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('time_spent_seconds', models.PositiveIntegerField(default=0)),
                ('is_completed', models.BooleanField(default=False)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('scenario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='progress_records', to='ar_training.arscenario')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ar_training_progress', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-updated_at',),
                'unique_together': {('user', 'scenario')},
            },
        ),
        migrations.CreateModel(
            name='ARHotspot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('hotspot_id', models.SlugField(max_length=80)),
                ('title', models.JSONField()),
                ('content', models.JSONField(default=dict)),
                ('position_yaw', models.FloatField(default=0)),
                ('position_pitch', models.FloatField(default=0)),
                ('icon_type', models.CharField(default='map-marker-question', max_length=40)),
                ('color_hint', models.CharField(blank=True, max_length=20)),
                ('order', models.PositiveIntegerField(default=0)),
                ('panorama', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hotspots', to='ar_training.arpanorama')),
            ],
            options={
                'ordering': ('panorama', 'order'),
                'unique_together': {('panorama', 'hotspot_id')},
            },
        ),
    ]
