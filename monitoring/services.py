import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone

try:
    from ultralytics import YOLO
except Exception:  # pragma: no cover - optional dependency during local setup
    YOLO = None

from .models import MonitorSession, ViolationAlert

RISK_CLASSES = {"plant_approaching"}
VIOLATION_CLASSES = {"plant_plucking", "animal_touching"}
EXPECTED_CLASSES = RISK_CLASSES | VIOLATION_CLASSES

_MODEL_CACHE = None


def _candidate_model_paths():
    base_dir = settings.BASE_DIR
    project_root = base_dir.parent
    ai_root = project_root / "ParkGuideAI"
    configured = os.getenv("MONITOR_MODEL_PATH", "").strip()

    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            ai_root / "runs/train/park_activity_v2/weights/best.pt",
            ai_root / "yolo11s.pt",
            ai_root / "yolo26n.pt",
        ]
    )
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


def _best_detection_for_frame(result, class_names):
    best = None

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

        if best is None:
            best = detection
            continue

        if detection["severity"] == "High" and best["severity"] != "High":
            best = detection
            continue

        if detection["confidence_score"] > best["confidence_score"]:
            best = detection

    return best


def analyze_uploaded_evidence(file_path, *, camera_source="phone-camera", source_mode="phone", guide_name="", location="", clip_duration="", clip_interval_minutes=None):
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
        "evidence_status": "AI analysis completed",
        "recommended_action": recommended_action,
        "details": f"Detected class: {class_name}. Source mode: {source_mode}.",
        "raw_payload": {
            "analysis_mode": "local_yolo",
            "source_mode": source_mode,
            "camera_source": camera_source,
            "clip_interval_minutes": clip_interval_minutes,
        },
    }


def upsert_active_session(user, *, source_mode="phone", camera_source="phone-camera", clip_interval_minutes=5):
    session = MonitorSession.objects.filter(user=user, is_active=True).order_by("-updated_at").first()
    if session is None:
        session = MonitorSession(user=user)

    session.is_active = True
    session.source_mode = source_mode or MonitorSession.SOURCE_PHONE
    session.camera_source = camera_source or "phone-camera"
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
