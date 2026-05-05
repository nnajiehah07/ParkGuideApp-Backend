# Generated manually for the monitor integration.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("secure_files", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MonitorSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_active", models.BooleanField(default=True)),
                ("source_mode", models.CharField(choices=[("phone", "Phone camera"), ("esp32", "ESP32 camera")], default="phone", max_length=24)),
                ("camera_source", models.CharField(blank=True, default="phone-camera", max_length=120)),
                ("clip_interval_minutes", models.PositiveSmallIntegerField(default=5)),
                ("last_clip_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="monitor_sessions", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={"ordering": ("-updated_at",)},
        ),
        migrations.CreateModel(
            name="ViolationAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_mode", models.CharField(choices=[("phone", "Phone camera"), ("esp32", "ESP32 camera")], default="phone", max_length=24)),
                ("title", models.CharField(max_length=200)),
                ("summary", models.CharField(max_length=255)),
                ("severity", models.CharField(choices=[("Low", "Low"), ("Medium", "Medium"), ("High", "High")], default="Medium", max_length=16)),
                ("status", models.CharField(choices=[("pending", "Pending review"), ("reviewed", "Reviewed"), ("resolved", "Resolved")], default="pending", max_length=16)),
                ("detected_activity", models.CharField(max_length=120)),
                ("detected_class", models.CharField(blank=True, max_length=120)),
                ("confidence_score", models.FloatField(blank=True, null=True)),
                ("camera_source", models.CharField(blank=True, default="phone-camera", max_length=120)),
                ("guide_name", models.CharField(blank=True, default="", max_length=120)),
                ("location", models.CharField(blank=True, default="", max_length=255)),
                ("captured_at", models.DateTimeField(auto_now_add=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("video_filename", models.CharField(blank=True, default="", max_length=255)),
                ("video_duration", models.CharField(blank=True, default="", max_length=32)),
                ("evidence_status", models.CharField(blank=True, default="Pending AI review", max_length=255)),
                ("recommended_action", models.CharField(blank=True, default="Review the returned footage and confirm whether intervention is needed.", max_length=255)),
                ("details", models.TextField(blank=True, default="")),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                (
                    "evidence_file",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="monitor_alerts", to="secure_files.securefile"),
                ),
                (
                    "session",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="alerts", to="monitoring.monitorsession"),
                ),
                (
                    "user",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="violation_alerts", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={"ordering": ("-received_at",)},
        ),
    ]
