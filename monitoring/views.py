import os
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from secure_files.services.firebase_storage import delete_file as delete_secure_blob, upload_file

from .models import MonitorSession, ViolationAlert
from .serializers import MonitorSessionSerializer, ViolationAlertSerializer
from .services import analyze_uploaded_evidence, stop_active_session, upsert_active_session


class MonitorStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        session = MonitorSession.objects.filter(user=request.user).order_by("-updated_at").first()
        alert_qs = ViolationAlert.objects.filter(user=request.user)
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
                "source": session.source_mode if session else MonitorSession.SOURCE_PHONE,
                "camera_source": session.camera_source if session else "phone-camera",
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
        source_mode = request.data.get("source_mode", MonitorSession.SOURCE_PHONE)
        camera_source = request.data.get("camera_source", "phone-camera")
        clip_interval_minutes = request.data.get("clip_interval_minutes", 5)

        session = upsert_active_session(
            request.user,
            source_mode=source_mode,
            camera_source=camera_source,
            clip_interval_minutes=clip_interval_minutes,
        )
        session.alert_count = ViolationAlert.objects.filter(user=request.user).count()
        session.last_alert_at = ViolationAlert.objects.filter(user=request.user).order_by("-received_at").values_list("received_at", flat=True).first()
        serializer = MonitorSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_200_OK)


class MonitorSessionStopView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        session = stop_active_session(request.user)
        if session is None:
            return Response({"localOnly": True, "sessionActive": False}, status=status.HTTP_200_OK)
        session.alert_count = ViolationAlert.objects.filter(user=request.user).count()
        session.last_alert_at = ViolationAlert.objects.filter(user=request.user).order_by("-received_at").values_list("received_at", flat=True).first()
        serializer = MonitorSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ViolationAlertViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ViolationAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = ViolationAlert.objects.select_related("session", "evidence_file")
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)


class MonitorEvidenceUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"detail": "Missing file field."}, status=status.HTTP_400_BAD_REQUEST)

        source_mode = request.data.get("source_mode", MonitorSession.SOURCE_PHONE)
        camera_source = request.data.get("camera_source", "phone-camera")
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

            uploaded.seek(0)
            secure_file = upload_file(uploaded=uploaded, owner=request.user)
            session = upsert_active_session(
                request.user,
                source_mode=source_mode,
                camera_source=camera_source,
                clip_interval_minutes=clip_interval_minutes,
            )
            session.last_clip_at = timezone.now()
            session.last_seen_at = timezone.now()
            session.save(update_fields=["last_clip_at", "last_seen_at", "updated_at"])

            analysis = analyze_uploaded_evidence(
                temp_path,
                camera_source=camera_source,
                source_mode=source_mode,
                guide_name=guide_name,
                location=location,
                clip_duration=clip_duration,
                clip_interval_minutes=clip_interval_minutes,
            )
            if analysis is None:
                try:
                    delete_secure_blob(secure_file.s3_key)
                except ImproperlyConfigured:
                    pass
                secure_file.delete()
                return Response(
                    {
                        "secure_file": None,
                        "deleted_after_processing": True,
                        "detail": "No violation was detected, so the uploaded clip was deleted after analysis.",
                    },
                    status=status.HTTP_201_CREATED,
                )

            alert = ViolationAlert.objects.create(
                user=request.user,
                session=session,
                evidence_file=secure_file,
                source_mode=source_mode,
                title=analysis["title"],
                summary=analysis["summary"],
                severity=analysis["severity"],
                status=analysis["status"],
                detected_activity=analysis["detected_activity"],
                detected_class=analysis.get("detected_class", ""),
                confidence_score=analysis.get("confidence_score"),
                camera_source=analysis.get("camera_source", camera_source),
                guide_name=analysis.get("guide_name", guide_name),
                location=analysis.get("location", location),
                video_filename=secure_file.original_name,
                video_duration=analysis.get("video_duration", clip_duration),
                evidence_status=analysis.get("evidence_status", "Pending AI review"),
                recommended_action=analysis.get("recommended_action", "Review the returned footage."),
                details=analysis.get("details", ""),
                raw_payload=analysis.get("raw_payload", {}),
            )
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
