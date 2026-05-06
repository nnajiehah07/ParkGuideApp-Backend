from rest_framework import serializers

from secure_files.serializers import SecureFileSerializer

from .models import MonitorSession, ViolationAlert


class MonitorSessionSerializer(serializers.ModelSerializer):
    alert_count = serializers.IntegerField(read_only=True)
    last_alert_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = MonitorSession
        fields = [
            "id",
            "user",
            "is_active",
            "source_mode",
            "camera_source",
            "clip_interval_minutes",
            "last_clip_at",
            "last_seen_at",
            "created_at",
            "updated_at",
            "alert_count",
            "last_alert_at",
        ]
        read_only_fields = ["id", "user", "created_at", "updated_at", "alert_count", "last_alert_at"]


class ViolationAlertSerializer(serializers.ModelSerializer):
    confidence = serializers.SerializerMethodField()
    video_url = serializers.SerializerMethodField()
    video_duration = serializers.CharField(required=False, allow_blank=True)
    video_filename = serializers.CharField(required=False, allow_blank=True)
    evidence_file = SecureFileSerializer(read_only=True)

    class Meta:
        model = ViolationAlert
        fields = [
            "id",
            "user",
            "session",
            "evidence_file",
            "source_mode",
            "title",
            "summary",
            "severity",
            "status",
            "detected_activity",
            "detected_class",
            "confidence_score",
            "confidence",
            "camera_source",
            "guide_name",
            "location",
            "captured_at",
            "received_at",
            "video_url",
            "video_filename",
            "video_duration",
            "evidence_status",
            "recommended_action",
            "details",
            "raw_payload",
        ]
        read_only_fields = fields

    def get_confidence(self, obj):
        if obj.confidence_score is None:
            return "N/A"
        return f"{obj.confidence_score:.0%}"

    def get_video_url(self, obj):
        if obj.evidence_file:
            return SecureFileSerializer().get_download_url(obj.evidence_file)
        return None
