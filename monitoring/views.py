import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from rest_framework import permissions, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MonitorSession, ViolationAlert
from .serializers import MonitorSessionSerializer, ViolationAlertSerializer
from .services import process_monitoring_clip, stop_active_session, upsert_active_session


class MonitorStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        session = MonitorSession.objects.filter(user=request.user).order_by("-updated_at").first()
        alert_qs = ViolationAlert.objects.all()
        latest_alert = alert_qs.order_by("-received_at").first()

        is_live = bool(session and session.is_active)
        state = "live" if is_live else "offline"
        if session and not session.is_active:
            state = "offline"
        elif session and session.last_seen_at is None:
            state = "checking"

        return Response(
            {
                "is_live": is_live,
                "state": state,
                "source": MonitorSession.SOURCE_ESP32,
                "camera_source": session.camera_source if session else "RE-CAM-01",
                "stream_url": None,
                "session_id": session.id if session else None,
                "alert_count": alert_qs.count(),
                "last_seen_at": session.last_seen_at if session else (latest_alert.received_at if latest_alert else None),
                "message": "Camera module is live." if is_live else "Camera module is not connected.",
                "clip_interval_minutes": session.clip_interval_minutes if session else 5,
            }
        )


class MonitorSessionStartView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        source_mode = MonitorSession.SOURCE_ESP32
        camera_source = request.data.get("camera_source", "RE-CAM-01")
        clip_interval_minutes = request.data.get("clip_interval_minutes", 5)

        session = upsert_active_session(
            request.user,
            source_mode=source_mode,
            camera_source=camera_source,
            clip_interval_minutes=clip_interval_minutes,
        )
        session.alert_count = ViolationAlert.objects.count()
        session.last_alert_at = ViolationAlert.objects.order_by("-received_at").values_list("received_at", flat=True).first()
        serializer = MonitorSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MonitorSessionStopView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        session = stop_active_session(request.user)
        if session is None:
            return Response({"localOnly": True, "sessionActive": False}, status=status.HTTP_200_OK)
        session.alert_count = ViolationAlert.objects.count()
        session.last_alert_at = ViolationAlert.objects.order_by("-received_at").values_list("received_at", flat=True).first()
        serializer = MonitorSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ViolationAlertViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ViolationAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ViolationAlert.objects.select_related("session", "evidence_file", "user").order_by("-received_at", "-id")


class MonitorEvidenceUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"detail": "Missing file field."}, status=status.HTTP_400_BAD_REQUEST)

        source_mode = MonitorSession.SOURCE_ESP32
        camera_source = request.data.get("camera_source", "RE-CAM-01")
        guide_name = request.data.get("guide_name", request.user.get_full_name() or request.user.get_username())
        location = request.data.get("location", "Field monitoring preview")
        clip_duration = request.data.get("clip_duration", request.data.get("video_duration", ""))
        clip_interval_minutes = request.data.get("clip_interval_minutes", 5)

        temp_path = None
        try:
            suffix = Path(uploaded.name).suffix or ".mp4"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                for chunk in uploaded.chunks():
                    temp_file.write(chunk)
                temp_path = Path(temp_file.name)

            result = process_monitoring_clip(
                temp_path,
                owner=request.user,
                uploaded_name=uploaded.name,
                content_type=getattr(uploaded, "content_type", "") or "",
                camera_source=camera_source,
                source_mode=source_mode,
                guide_name=guide_name,
                location=location,
                clip_duration=clip_duration,
                clip_interval_minutes=clip_interval_minutes,
                annotate=False,
                send_notifications=True,
                created_by=request.user if request.user.is_staff else None,
            )
            if result["alert"] is None:
                return Response(
                    {
                        "secure_file": None,
                        "deleted_after_processing": True,
                        "detail": "No violation was detected, so the uploaded ESP32 clip was deleted after analysis.",
                    },
                    status=status.HTTP_201_CREATED,
                )

            secure_file = result["secure_file"]
            alert = result["alert"]
            serializer = ViolationAlertSerializer(alert)
            return Response(
                {
                    "secure_file": secure_file.id,
                    "alert": serializer.data,
                },
                status=status.HTTP_201_CREATED,
            )
        except ImproperlyConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
