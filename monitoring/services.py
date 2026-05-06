import os
import mimetypes
import subprocess
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.utils import timezone

from notifications.services import create_notification_for_staff, create_notification_for_user, create_notification_for_users
from secure_files.services.firebase_storage import delete_file as delete_secure_blob, upload_file

try:
    from ultralytics import YOLO
except Exception:  # pragma: no cover - optional dependency during local setup
    YOLO = None

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency during local setup
    cv2 = None

from .models import MonitorSession, ViolationAlert

RISK_CLASSES = {"plant_approaching"}
VIOLATION_CLASSES = {"plant_plucking", "animal_touching"}
EXPECTED_CLASSES = RISK_CLASSES | VIOLATION_CLASSES

_MODEL_CACHE = None


def _candidate_model_paths():
    base_dir = settings.BASE_DIR
    # Prefer an explicitly configured model path (env var or Django setting)
    configured = os.getenv("MONITOR_MODEL_PATH", "").strip()
    if not configured:
        configured = getattr(settings, "MONITOR_MODEL_PATH", "") or ""

    candidates = []
    if configured:
        candidates.append(Path(configured))

    # Primary local locations inside the backend project (recommended for cloud deploys)
    # - <BASE_DIR>/models/monitor.pt
    # - <BASE_DIR>/monitoring/models/best.pt
    # - <BASE_DIR>/monitoring/model.pt
    candidates.extend(
        [
            base_dir / "models" / "monitor.pt",
            base_dir / "monitoring" / "models" / "best.pt",
            base_dir / "monitoring" / "model.pt",
        ]
    )

    # Backwards-compat fallback to sibling AI repo (kept for local dev convenience)
    try:
        project_root = base_dir.parent
        ai_root = project_root / "ParkGuideAI"
        candidates.extend([
            ai_root / "runs/train/park_activity_v2/weights/best.pt",
            ai_root / "yolo11s.pt",
            ai_root / "yolo26n.pt",
        ])
    except Exception:
        # safe-ignore if BASE_DIR isn't a Path-like object
        pass

    return candidates


def load_detection_model():
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if YOLO is None:
        return None

    for candidate in _candidate_model_paths():
        if candidate.exists():
            _MODEL_CACHE = YOLO(str(candidate))
            return _MODEL_CACHE
    return None


def _class_names_from_model(model):
    names = getattr(model, "names", None)
    if isinstance(names, dict):
        return [names[index] for index in sorted(names)]
    if isinstance(names, (list, tuple)):
        return list(names)
    return []


def _detections_for_frame(result, class_names):
    detections = []

    for box in getattr(result, "boxes", []):
        class_id = int(box.cls[0])
        confidence = float(box.conf[0])
        if class_id >= len(class_names):
            continue
        class_name = class_names[class_id]
        if class_name not in EXPECTED_CLASSES:
            continue

        severity = "High" if class_name in VIOLATION_CLASSES else "Medium"
        detection = {
            "detected_activity": class_name.replace("_", " ").title(),
            "detected_class": class_name,
            "severity": severity,
            "confidence_score": confidence,
        }
        if getattr(box, "xyxy", None) is not None:
            detection["bbox"] = [float(value) for value in box.xyxy[0]]
        detections.append(detection)

    return detections


def _best_detection_from_list(detections):
    best = None

    for detection in detections:
        candidate = {key: value for key, value in detection.items() if key != "bbox"}

        if best is None:
            best = candidate
            continue

        if candidate["severity"] == "High" and best["severity"] != "High":
            best = candidate
            continue

        if candidate["confidence_score"] > best["confidence_score"]:
            best = candidate

    return best


def _best_detection_for_frame(result, class_names):
    return _best_detection_from_list(_detections_for_frame(result, class_names))


def _draw_detection_boxes(frame, detections):
    if cv2 is None:
        return frame

    annotated = frame.copy()
    for detection in detections:
        bbox = detection.get("bbox")
        if not bbox:
            continue

        x1, y1, x2, y2 = [int(value) for value in bbox]
        color = (35, 35, 220) if detection["severity"] == "High" else (0, 180, 255)
        label = f'{detection["detected_activity"]} {detection["confidence_score"]:.0%}'
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 3)

        label_size, baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        label_y = max(y1, label_size[1] + 10)
        cv2.rectangle(
            annotated,
            (x1, label_y - label_size[1] - baseline - 8),
            (x1 + label_size[0] + 10, label_y + baseline - 2),
            color,
            -1,
        )
        cv2.putText(
            annotated,
            label,
            (x1 + 5, label_y - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    return annotated


def _make_browser_playable_mp4(video_path):
    output_path = video_path.with_name(f"{video_path.stem}_browser.mp4")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(output_path),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return video_path

    try:
        video_path.unlink()
    except OSError:
        pass
    return output_path


def _analyze_video_with_annotations(model, file_path, class_names):
    if cv2 is None:
        return None, None

    capture = cv2.VideoCapture(str(file_path))
    if not capture.isOpened():
        return None, None

    fps = capture.get(cv2.CAP_PROP_FPS) or 20
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        return None, None

    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    output_path = Path(output_file.name)
    output_file.close()

    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        try:
            output_path.unlink()
        except OSError:
            pass
        return None, None

    best_detection = None
    detection_counts = {}
    annotated_frames = 0

    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break

            results = model.predict(source=frame, conf=0.25, verbose=False, save=False)
            result = results[0] if results else None
            detections = _detections_for_frame(result, class_names) if result is not None else []
            if detections:
                annotated_frames += 1
                for detection in detections:
                    detected_class = detection["detected_class"]
                    detection_counts[detected_class] = detection_counts.get(detected_class, 0) + 1
                current = _best_detection_from_list(detections)
                if best_detection is None:
                    best_detection = current
                elif current["severity"] == "High" and best_detection["severity"] != "High":
                    best_detection = current
                elif current["confidence_score"] > best_detection["confidence_score"]:
                    best_detection = current

            writer.write(_draw_detection_boxes(frame, detections))
    finally:
        capture.release()
        writer.release()

    if best_detection is None:
        try:
            output_path.unlink()
        except OSError:
            pass
        return None, None

    output_path = _make_browser_playable_mp4(output_path)

    return best_detection, {
        "path": output_path,
        "annotated_frames": annotated_frames,
        "detection_counts": detection_counts,
    }


def analyze_uploaded_evidence(file_path, *, camera_source="RE-CAM-01", source_mode=MonitorSession.SOURCE_ESP32, guide_name="", location="", clip_duration="", clip_interval_minutes=None, annotate=False):
    model = load_detection_model()
    if model is None:
        return {
            "title": "Returned footage ready for review",
            "summary": "The uploaded clip has been stored and is waiting for AI analysis.",
            "severity": "Medium",
            "status": ViolationAlert.STATUS_PENDING,
            "detected_activity": "Pending AI analysis",
            "detected_class": "",
            "confidence_score": None,
            "camera_source": camera_source,
            "guide_name": guide_name,
            "location": location,
            "video_duration": clip_duration,
            "evidence_status": "AI model not configured on the backend yet.",
            "recommended_action": "Connect the AI model or service to classify this clip automatically.",
            "details": "AI inference is not available in the current backend environment, so the clip is queued as a review item.",
            "raw_payload": {"analysis_mode": "fallback"},
        }

    class_names = _class_names_from_model(model)
    if not class_names:
        return None

    annotation = None
    if annotate:
        best_detection, annotation = _analyze_video_with_annotations(model, file_path, class_names)
    if not annotate or (annotate and best_detection is None and annotation is None):
        predictions = model.predict(source=str(file_path), conf=0.25, stream=True, verbose=False, save=False)
        best_detection = None
        for result in predictions:
            current = _best_detection_for_frame(result, class_names)
            if current is None:
                continue
            if best_detection is None:
                best_detection = current
                continue
            if current["severity"] == "High" and best_detection["severity"] != "High":
                best_detection = current
                continue
            if current["confidence_score"] > best_detection["confidence_score"]:
                best_detection = current

    if best_detection is None:
        return None

    severity = best_detection["severity"]
    class_name = best_detection["detected_class"]
    summary = "A possible violation was detected from the uploaded footage." if severity == "High" else "A potential risk was detected from the uploaded footage."
    recommended_action = "Review the footage and confirm whether intervention or escalation is required."
    if severity == "Medium":
        recommended_action = "Review the footage and decide whether any preventive action is needed."

    return {
        "title": "AI detection alert",
        "summary": summary,
        "severity": severity,
        "status": ViolationAlert.STATUS_PENDING,
        "detected_activity": best_detection["detected_activity"],
        "detected_class": class_name,
        "confidence_score": best_detection["confidence_score"],
        "camera_source": camera_source,
        "guide_name": guide_name,
        "location": location,
        "video_duration": clip_duration,
        "evidence_status": "AI analysis completed with bounding boxes" if annotation else "AI analysis completed",
        "recommended_action": recommended_action,
        "details": f"Detected class: {class_name}. Source mode: {source_mode}.",
        "raw_payload": {
            "analysis_mode": "local_yolo",
            "source_mode": source_mode,
            "camera_source": camera_source,
            "clip_interval_minutes": clip_interval_minutes,
            "annotated_frames": annotation.get("annotated_frames") if annotation else None,
            "detection_counts": annotation.get("detection_counts") if annotation else {},
        },
        "annotated_video_path": str(annotation["path"]) if annotation else "",
    }


def upsert_active_session(user, *, source_mode=MonitorSession.SOURCE_ESP32, camera_source="RE-CAM-01", clip_interval_minutes=5):
    session = MonitorSession.objects.filter(user=user, is_active=True).order_by("-updated_at").first()
    if session is None:
        session = MonitorSession(user=user)

    session.is_active = True
    session.source_mode = MonitorSession.SOURCE_ESP32
    session.camera_source = camera_source or "RE-CAM-01"
    session.clip_interval_minutes = max(int(clip_interval_minutes or 5), 1)
    session.last_seen_at = timezone.now()
    session.save()
    return session


def stop_active_session(user):
    session = MonitorSession.objects.filter(user=user, is_active=True).order_by("-updated_at").first()
    if session is None:
        session = MonitorSession.objects.filter(user=user).order_by("-updated_at").first()
    if session is None:
        return None
    session.is_active = False
    session.last_seen_at = timezone.now()
    session.save()
    return session


def get_monitoring_owner(preferred_user=None):
    if preferred_user is not None:
        return preferred_user

    User = get_user_model()
    return (
        User.objects.filter(is_active=True, is_staff=True).order_by("id").first()
        or User.objects.filter(is_active=True, is_superuser=True).order_by("id").first()
        or User.objects.filter(is_active=True).order_by("id").first()
    )


def _upload_path_to_secure_file(file_path, *, owner, name=None, content_type=None):
    file_path = Path(file_path)
    upload_name = name or file_path.name
    guessed_content_type = content_type or mimetypes.guess_type(upload_name)[0] or "application/octet-stream"

    with file_path.open("rb") as raw_file:
        upload = File(raw_file, name=upload_name)
        upload.content_type = guessed_content_type
        upload.size = file_path.stat().st_size
        return upload_file(uploaded=upload, owner=owner)


def _delete_secure_file(secure_file):
    if secure_file is None:
        return
    try:
        delete_secure_blob(secure_file.s3_key)
    except Exception:
        pass
    secure_file.delete()


def notify_monitoring_alert(alert, *, created_by=None, include_guide=True):
    confidence = "N/A" if alert.confidence_score is None else f"{alert.confidence_score:.0%}"
    title = f"{alert.severity} AI monitoring alert"
    description = f"{alert.detected_activity} detected at {alert.location or alert.camera_source}."
    full_text = (
        f"{alert.summary}\n\n"
        f"Activity: {alert.detected_activity}\n"
        f"Severity: {alert.severity}\n"
        f"Confidence: {confidence}\n"
        f"Source: {alert.camera_source}\n"
        f"Guide: {alert.guide_name or alert.user.get_full_name() or alert.user.get_username()}\n"
        f"Recommended action: {alert.recommended_action}"
    )
    push_data = {
        "type": "monitoring_alert",
        "monitoring_alert_id": str(alert.id),
        "alert_id": str(alert.id),
    }

    create_notification_for_staff(
        title=title,
        description=description,
        full_text=full_text,
        created_by=created_by,
        related_user=alert.user,
        send_push=True,
        push_data=push_data,
    )

    if not include_guide:
        return

    if alert.user and not alert.user.is_staff and not alert.user.is_superuser:
        create_notification_for_user(
            user=alert.user,
            title=title,
            description=description,
            full_text=full_text,
            created_by=created_by,
            related_user=alert.user,
            send_push=True,
            push_data=push_data,
        )
        return

    User = get_user_model()
    guide_users = User.objects.filter(is_active=True, is_staff=False, is_superuser=False)
    create_notification_for_users(
        users=guide_users,
        title=title,
        description=description,
        full_text=full_text,
        created_by=created_by,
        related_user=alert.user,
        send_push=True,
        push_data=push_data,
    )


def process_monitoring_clip(
    file_path,
    *,
    owner=None,
    uploaded_name=None,
    content_type=None,
    source_mode=MonitorSession.SOURCE_ESP32,
    camera_source="RE-CAM-01",
    guide_name="",
    location="Field monitoring preview",
    clip_duration="",
    clip_interval_minutes=5,
    annotate=False,
    send_notifications=True,
    created_by=None,
):
    owner = get_monitoring_owner(owner)
    if owner is None:
        raise ImproperlyConfigured("Monitoring alerts need at least one active user to own the evidence.")

    source_mode = MonitorSession.SOURCE_ESP32

    try:
        clip_interval_minutes = max(int(clip_interval_minutes or 5), 1)
    except (TypeError, ValueError):
        clip_interval_minutes = 5

    file_path = Path(file_path)
    upload_name = uploaded_name or file_path.name
    upload_content_type = content_type or mimetypes.guess_type(upload_name)[0] or "video/mp4"
    raw_secure_file = _upload_path_to_secure_file(
        file_path,
        owner=owner,
        name=upload_name,
        content_type=upload_content_type,
    )

    try:
        analysis = analyze_uploaded_evidence(
            file_path,
            camera_source=camera_source,
            source_mode=source_mode,
            guide_name=guide_name,
            location=location,
            clip_duration=clip_duration,
            clip_interval_minutes=clip_interval_minutes,
            annotate=annotate,
        )
    except Exception:
        _delete_secure_file(raw_secure_file)
        raise

    if analysis is None:
        _delete_secure_file(raw_secure_file)
        return {
            "alert": None,
            "secure_file": None,
            "session": None,
            "analysis": None,
            "deleted_after_processing": True,
        }

    annotated_video_path = analysis.get("annotated_video_path")
    secure_file = raw_secure_file
    raw_evidence_replaced = False
    if annotated_video_path:
        processed_name = f"{Path(upload_name).stem}_ai_boxes.mp4"
        processed_path = Path(annotated_video_path)
        if processed_path.exists():
            secure_file = _upload_path_to_secure_file(
                processed_path,
                owner=owner,
                name=processed_name,
                content_type="video/mp4",
            )
            _delete_secure_file(raw_secure_file)
            raw_evidence_replaced = True

    session = upsert_active_session(
        owner,
        source_mode=source_mode,
        camera_source=camera_source,
        clip_interval_minutes=clip_interval_minutes,
    )
    session.last_clip_at = timezone.now()
    session.last_seen_at = timezone.now()
    session.save(update_fields=["last_clip_at", "last_seen_at", "updated_at"])

    alert = ViolationAlert.objects.create(
        user=owner,
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
        raw_payload={
            **(analysis.get("raw_payload", {}) or {}),
            "raw_evidence_uploaded_first": True,
            "raw_evidence_replaced_by_processed_clip": raw_evidence_replaced,
        },
    )

    if send_notifications:
        try:
            notify_monitoring_alert(alert, created_by=created_by)
        except Exception as exc:
            alert.raw_payload = {
                **(alert.raw_payload or {}),
                "notification_error": str(exc),
            }
            alert.save(update_fields=["raw_payload"])

    if annotated_video_path:
        try:
            Path(annotated_video_path).unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "alert": alert,
        "secure_file": secure_file,
        "session": session,
        "analysis": analysis,
        "deleted_after_processing": False,
    }
