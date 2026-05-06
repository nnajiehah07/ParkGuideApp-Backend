from django.conf import settings
from django.db import models

from secure_files.models import SecureFile


class MonitorSession(models.Model):
    SOURCE_PHONE = "phone"
    SOURCE_ESP32 = "esp32"
    SOURCE_CHOICES = [
        (SOURCE_PHONE, "Phone camera"),
        (SOURCE_ESP32, "ESP32 camera"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="monitor_sessions")
    is_active = models.BooleanField(default=True)
    source_mode = models.CharField(max_length=24, choices=SOURCE_CHOICES, default=SOURCE_ESP32)
    camera_source = models.CharField(max_length=120, blank=True, default="RE-CAM-01")
    clip_interval_minutes = models.PositiveSmallIntegerField(default=5)
    last_clip_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self):
        return f"{self.user} - {self.source_mode}"


class ViolationAlert(models.Model):
    STATUS_PENDING = "pending"
    STATUS_REVIEWED = "reviewed"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending review"),
        (STATUS_REVIEWED, "Reviewed"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    SEVERITY_LOW = "Low"
    SEVERITY_MEDIUM = "Medium"
    SEVERITY_HIGH = "High"
    SEVERITY_CHOICES = [
        (SEVERITY_LOW, "Low"),
        (SEVERITY_MEDIUM, "Medium"),
        (SEVERITY_HIGH, "High"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="violation_alerts")
    session = models.ForeignKey(MonitorSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="alerts")
    evidence_file = models.ForeignKey(SecureFile, null=True, blank=True, on_delete=models.SET_NULL, related_name="monitor_alerts")
    source_mode = models.CharField(max_length=24, choices=MonitorSession.SOURCE_CHOICES, default=MonitorSession.SOURCE_ESP32)
    title = models.CharField(max_length=200)
    summary = models.CharField(max_length=255)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    detected_activity = models.CharField(max_length=120)
    detected_class = models.CharField(max_length=120, blank=True)
    confidence_score = models.FloatField(null=True, blank=True)
    camera_source = models.CharField(max_length=120, blank=True, default="RE-CAM-01")
    guide_name = models.CharField(max_length=120, blank=True, default="")
    location = models.CharField(max_length=255, blank=True, default="")
    captured_at = models.DateTimeField(auto_now_add=True)
    received_at = models.DateTimeField(auto_now_add=True)
    video_filename = models.CharField(max_length=255, blank=True, default="")
    video_duration = models.CharField(max_length=32, blank=True, default="")
    evidence_status = models.CharField(max_length=255, blank=True, default="Pending AI review")
    recommended_action = models.CharField(max_length=255, blank=True, default="Review the returned footage and confirm whether intervention is needed.")
    details = models.TextField(blank=True, default="")
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-received_at",)

    def __str__(self):
        return f"{self.title} ({self.severity})"
