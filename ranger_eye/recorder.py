import os
import shutil
import subprocess
import tempfile
import time

import cv2
import imageio_ffmpeg
from django.conf import settings
from django.utils import timezone

from monitoring.models import MonitorSession
from monitoring.services import process_monitoring_clip

from .models import RangerEyeRecorderStatus, RangerEyeRecording


# =========================
# ESP32-CAM stream URL
# =========================
CAMERA_STREAM_URL = "http://172.20.10.10:81/stream"

# =========================
# Recording settings
# =========================
VIDEO_DURATION_SECONDS = 15
RECORD_EVERY_SECONDS = 30

VIDEO_FPS = 8
VIDEO_WIDTH = 320
VIDEO_HEIGHT = 240

MIN_VALID_FRAMES = 5
MIN_VIDEO_SIZE_BYTES = 10000


def get_recorder_status():
    status, _ = RangerEyeRecorderStatus.objects.get_or_create(pk=1)
    return status


def update_status(*, running=None, message=None, last_recording=None, next_recording=None):
    status = get_recorder_status()

    if running is not None:
        status.running = running

    if message is not None:
        status.message = message

    if last_recording is not None:
        status.last_recording = last_recording

    if next_recording is not None:
        status.next_recording = next_recording

    status.save()
    return status


def delete_file_if_exists(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        print(f"Could not delete file {path}: {exc}")


def open_camera_stream():
    cap = cv2.VideoCapture(CAMERA_STREAM_URL)

    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    return cap


def encode_frames_to_mp4(frames_dir, output_path):
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    input_pattern = os.path.join(frames_dir, "frame_%04d.jpg")

    command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",

        "-framerate",
        str(VIDEO_FPS),

        "-i",
        input_pattern,

        "-vf",
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}",

        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",

        output_path,
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )

    if result.stderr:
        print("FFmpeg encode message:")
        print(result.stderr)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg encoding failed with code {result.returncode}")


def record_one_video():
    update_status(
        running=True,
        message="Recording browser-playable video from ESP32-CAM...",
        next_recording="Recording now",
    )

    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"recording_{timestamp}.mp4"

    relative_path = os.path.join("ranger_eye", "recordings", filename)
    output_dir = os.path.join(settings.MEDIA_ROOT, "ranger_eye", "recordings")
    output_path = os.path.join(output_dir, filename)

    os.makedirs(output_dir, exist_ok=True)

    frames_dir = tempfile.mkdtemp(prefix="ranger_eye_frames_")

    print("================================")
    print(f"Starting RangerEye recording: {filename}")
    print(f"Camera stream: {CAMERA_STREAM_URL}")
    print(f"Target duration: {VIDEO_DURATION_SECONDS} seconds")
    print(f"FPS: {VIDEO_FPS}")
    print("================================")

    cap = None
    valid_frames = 0
    saved_frames = 0
    last_good_frame = None

    target_frames = VIDEO_DURATION_SECONDS * VIDEO_FPS
    frame_interval = 1.0 / VIDEO_FPS

    try:
        cap = open_camera_stream()

        for frame_number in range(1, target_frames + 1):
            loop_start = time.time()

            if cap is None or not cap.isOpened():
                print("Camera stream not open. Reconnecting...")
                if cap is not None:
                    cap.release()
                time.sleep(0.3)
                cap = open_camera_stream()

            ret, frame = cap.read() if cap is not None else (False, None)

            if ret and frame is not None:
                frame = cv2.resize(frame, (VIDEO_WIDTH, VIDEO_HEIGHT))
                last_good_frame = frame
                valid_frames += 1
            else:
                print("Frame read failed. Using previous frame if available.")

                if cap is not None:
                    cap.release()
                cap = None

                if last_good_frame is not None:
                    frame = last_good_frame.copy()
                else:
                    frame = None

            if frame is None:
                # Create black frame if camera has not produced anything yet
                frame = 0 * cv2.UMat(VIDEO_HEIGHT, VIDEO_WIDTH, cv2.CV_8UC3).get()

            frame_path = os.path.join(frames_dir, f"frame_{frame_number:04d}.jpg")
            cv2.imwrite(frame_path, frame)
            saved_frames += 1

            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed

            if sleep_time > 0:
                time.sleep(sleep_time)

        print(f"Captured frames: {saved_frames}")
        print(f"Valid camera frames: {valid_frames}")

        if valid_frames < MIN_VALID_FRAMES:
            update_status(
                running=False,
                message=f"Not enough real camera frames: {valid_frames}. Video discarded.",
            )
            print(f"Not enough real frames: {valid_frames}")
            return

        update_status(
            running=True,
            message=f"Encoding {saved_frames} frames into MP4...",
        )

        encode_frames_to_mp4(frames_dir, output_path)

        if not os.path.exists(output_path):
            update_status(
                running=False,
                message="Video file was not created.",
            )
            print("ERROR: Video file was not created.")
            return

        file_size = os.path.getsize(output_path)

        if file_size < MIN_VIDEO_SIZE_BYTES:
            update_status(
                running=False,
                message=f"Video file too small: {file_size} bytes.",
            )
            delete_file_if_exists(output_path)
            print(f"Deleted invalid video. Size: {file_size}")
            return

        RangerEyeRecording.objects.create(
            filename=filename,
            video_file=relative_path.replace("\\", "/"),
            duration_seconds=VIDEO_DURATION_SECONDS,
            source="ESP32-CAM frame recorder",
        )

        update_status(
            running=True,
            message=f"Running AI analysis for {filename}...",
        )

        try:
            result = process_monitoring_clip(
                output_path,
                uploaded_name=filename,
                content_type="video/mp4",
                source_mode=MonitorSession.SOURCE_ESP32,
                camera_source="RE-CAM-01",
                guide_name="RangerEye ESP32-CAM",
                location="Guided Tour Route",
                clip_duration=f"{VIDEO_DURATION_SECONDS}s",
                clip_interval_minutes=max(RECORD_EVERY_SECONDS // 60, 1),
                annotate=True,
                send_notifications=True,
            )
            if result["alert"] is None:
                ai_message = "AI analysis completed: no monitored activity detected."
            else:
                alert = result["alert"]
                confidence = "N/A" if alert.confidence_score is None else f"{alert.confidence_score:.0%}"
                ai_message = f"AI alert #{alert.id}: {alert.detected_activity} ({alert.severity}, {confidence})."
        except Exception as exc:
            ai_message = f"Saved video, but AI alert processing failed: {exc}"
            print(ai_message)

        update_status(
            running=False,
            message=f"Saved {filename}. {ai_message}",
            last_recording=filename,
        )

        print("================================")
        print(f"Saved video: {filename}")
        print(f"File size: {file_size} bytes")
        print(f"Valid frames: {valid_frames}")
        print(f"Saved frames: {saved_frames}")
        print("================================")

    except Exception as exc:
        update_status(
            running=False,
            message=f"Recording error: {exc}",
        )
        delete_file_if_exists(output_path)
        print(f"ERROR: Recording failed: {exc}")

    finally:
        if cap is not None:
            cap.release()

        shutil.rmtree(frames_dir, ignore_errors=True)
        update_status(running=False)


def run_recording_loop():
    update_status(
        running=False,
        message="Video recorder starting...",
        next_recording=f"In {RECORD_EVERY_SECONDS} seconds",
    )

    print("RangerEye recorder loop started.")
    print("Press Ctrl+C to stop.")

    time.sleep(5)

    while True:
        record_one_video()

        update_status(
            running=False,
            next_recording=f"In {RECORD_EVERY_SECONDS} seconds",
        )

        print(f"Next recording in {RECORD_EVERY_SECONDS} seconds")
        time.sleep(RECORD_EVERY_SECONDS)
