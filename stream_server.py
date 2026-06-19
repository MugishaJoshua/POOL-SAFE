import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
os.environ.pop('DATABASE_URL', None)  # force local PostgreSQL, ignore cloud URL
django.setup()

from flask import Flask, Response
from ultralytics import YOLO
from dashboard.models import DetectionEvent, Notification
from dashboard.tasks import send_realtime_alert, send_daily_digest
from django.utils import timezone
import cv2
import requests
import time
import threading
import numpy as np

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_PATH       = "best.pt"
DJANGO_URL       = "https://pool-guard.onrender.com/api/ingest/"
CONFIDENCE       = 0.40
FRAME_INTERVAL   = 2

DETECT_THRESHOLD = 0.80
ALERT_THRESHOLD  = 0.80

CAMERA_SOURCE    = 0

DIGEST_HOUR      = 8   # Send daily digest at 08:00 Kigali time

# ── Class config ──────────────────────────────────────────────────────────────
SEVERITY_MAP = {
    "Animal": "high",
    "Food":   "high",
    "Trash":  "medium",
    "Bottle": "low",
}

CLASS_MAP = {
    "Animal": "animal",
    "Food":   "food",
    "Trash":  "trash",
    "Bottle": "bottle",
}

CLASS_MESSAGES = {
    "animal": "Animal intrusion detected! Escort animal away from pool area.",
    "food":   "Food remains spotted at pool perimeter. Collect before attracting pests.",
    "trash":  "Trash detected near the pool. Please remove immediately.",
    "bottle": "Plastic bottle found near pool edge. Remove to prevent water contamination.",
}

THREAT_CLASSES = set(CLASS_MAP.keys())

# ── Local PostgreSQL helpers ──────────────────────────────────────────────────

def save_local(payload):
    """Save detection to local PostgreSQL via Django ORM. Returns event id or None."""
    try:
        event = DetectionEvent.objects.create(
            object_class    = payload['object_class'],
            confidence      = payload['confidence'],
            severity        = payload['severity'],
            location_note   = payload['location_note'],
            synced_to_cloud = False,
        )
        message = CLASS_MESSAGES.get(payload['object_class'], f"{payload['object_class']} detected.")
        Notification.objects.create(event=event, message=message)
        return event.id
    except Exception as e:
        print(f"  ❌ Local save failed: {e}")
        return None


def mark_synced(event_id):
    try:
        DetectionEvent.objects.filter(id=event_id).update(synced_to_cloud=True)
    except Exception as e:
        print(f"  ❌ Mark synced failed: {e}")


def flush_queue():
    """Background thread: retry unsynced local detections every 30 seconds."""
    while True:
        time.sleep(30)
        try:
            pending = DetectionEvent.objects.filter(synced_to_cloud=False)
            count = pending.count()
            if count:
                print(f"\n  🔄 Syncing {count} pending detection(s) to cloud...")

            for event in pending:
                payload = {
                    "object_class":  event.object_class,
                    "confidence":    float(event.confidence),
                    "location_note": event.location_note,
                    "severity":      event.severity,
                }
                try:
                    r = requests.post(DJANGO_URL, json=payload, timeout=10)
                    if r.status_code == 201:
                        event.synced_to_cloud = True
                        event.save()
                        print(f"  ✅ Synced event #{event.id} to cloud")
                except Exception:
                    pass  # will retry next cycle

        except Exception as e:
            print(f"  ❌ Flush error: {e}")


def daily_digest_scheduler():
    """Background thread: fire daily digest once at DIGEST_HOUR every day."""
    sent_today = None
    while True:
        now = timezone.now()
        if now.hour == DIGEST_HOUR and now.date() != sent_today:
            print(f"\n  📅 Triggering daily digest...")
            threading.Thread(target=send_daily_digest, daemon=True).start()
            sent_today = now.date()
        time.sleep(60)  # check every minute


# ── Camera helpers ────────────────────────────────────────────────────────────

def open_camera(source=CAMERA_SOURCE):
    if isinstance(source, str):
        backends = [
            (cv2.CAP_FFMPEG, "FFMPEG"),
            (cv2.CAP_ANY,    "CAP_ANY"),
        ]
    else:
        backends = [
            (cv2.CAP_DSHOW, "DirectShow"),
            (cv2.CAP_MSMF,  "MSMF"),
            (cv2.CAP_ANY,   "CAP_ANY"),
        ]

    for backend, name in backends:
        print(f"  Trying {name}...", end=" ", flush=True)
        cap = cv2.VideoCapture(source, backend)
        if not cap.isOpened():
            cap.release()
            print("failed to open")
            continue

        ok, _ = cap.read()
        if not ok:
            cap.release()
            print("opened but no frames — skipping")
            continue

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, 30)
        print(f"✅  ({name})")
        return cap

    raise RuntimeError(f"Cannot open camera source: {source}")


# ── Shared state ──────────────────────────────────────────────────────────────
last_sent     = {}
frame_lock    = threading.Lock()
current_frame = None
camera_ok     = threading.Event()

print("Loading PoolGuard model...")
model = YOLO(MODEL_PATH)
print(f"Model loaded ✅  |  Classes: {list(model.names.values())}")
print(f"Thresholds — detect: ≥{DETECT_THRESHOLD:.0%}  |  alert: ≥{ALERT_THRESHOLD:.0%}\n")


# ── Detection / capture loop ──────────────────────────────────────────────────

def detection_loop():
    global current_frame

    while True:
        cap = None
        while cap is None:
            try:
                print("Opening camera...")
                cap = open_camera(CAMERA_SOURCE)
                camera_ok.set()
                print("Camera is live ✅\n")
            except RuntimeError as e:
                print(f"❌  {e}  — retrying in 5 s...")
                camera_ok.clear()
                time.sleep(5)

        consecutive_failures = 0
        while True:
            ret, frame = cap.read()

            if not ret:
                consecutive_failures += 1
                if consecutive_failures >= 20:
                    print("⚠️  Too many frame failures — reopening camera...")
                    cap.release()
                    camera_ok.clear()
                    cap = None
                    break
                time.sleep(0.05)
                continue

            consecutive_failures = 0
            current_time = time.time()

            results   = model(frame, conf=CONFIDENCE, iou=0.45, imgsz=640, verbose=False)
            annotated = results[0].plot()

            with frame_lock:
                current_frame = annotated.copy()

            for result in results:
                for box in result.boxes:
                    class_id   = int(box.cls[0])
                    confidence = float(box.conf[0])
                    label      = model.names[class_id]

                    if label not in THREAT_CLASSES:
                        continue
                    if confidence < DETECT_THRESHOLD:
                        continue

                    severity     = SEVERITY_MAP[label]
                    object_class = CLASS_MAP[label]

                    print(f"  👁  {label} → {object_class}  {confidence:.0%}  [{severity}]", end="")

                    if confidence < ALERT_THRESHOLD:
                        print("  (detected — below alert threshold)")
                        continue

                    if current_time - last_sent.get(label, 0) < FRAME_INTERVAL:
                        print("  (cooldown)")
                        continue

                    payload = {
                        "object_class":  object_class,
                        "confidence":    round(confidence, 4),
                        "location_note": f"CAM-01: {label}",
                        "severity":      severity,
                    }

                    # ── Always save to local PostgreSQL first ─────────────────
                    event_id = save_local(payload)
                    if event_id:
                        print(f"\n  💾 Saved locally (#{event_id}): {label} [{severity}]")

                    # ── Send real-time email alert (non-blocking) ─────────────
                    threading.Thread(
                        target=send_realtime_alert,
                        args=(object_class, round(confidence, 4), severity, f"CAM-01: {label}"),
                        daemon=True
                    ).start()

                    # ── Then try cloud ────────────────────────────────────────
                    try:
                        r = requests.post(DJANGO_URL, json=payload, timeout=10)
                        if r.status_code == 201:
                            if event_id:
                                mark_synced(event_id)
                            print(f"  🚨 ALERT SENT to cloud: {label} → {object_class}  {confidence:.0%}  [{severity}]")
                        else:
                            error_detail = r.json().get('error', '?') if 'json' in r.headers.get('Content-Type', '') else f"HTTP {r.status_code}"
                            print(f"  ⚠️  Cloud failed: {error_detail} — will retry in 30s")
                    except Exception:
                        print(f"  📦 Offline — will sync to cloud when back online")

                    last_sent[label] = current_time

            time.sleep(0.05)


# ── MJPEG stream generator ────────────────────────────────────────────────────

PLACEHOLDER = None

def _make_placeholder():
    img = np.zeros((480, 640, 3), dtype="uint8")
    img[:] = (30, 30, 30)
    cv2.putText(img, "Camera Offline",  (160, 230),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, (80, 80, 80), 3)
    cv2.putText(img, "Reconnecting...", (175, 270),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 60), 2)
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return buf.tobytes()


def generate():
    global PLACEHOLDER
    while True:
        with frame_lock:
            frame = current_frame

        if frame is None:
            if PLACEHOLDER is None:
                PLACEHOLDER = _make_placeholder()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + PLACEHOLDER + b'\r\n')
            time.sleep(0.5)
            continue

        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        time.sleep(0.033)


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route('/stream')
def stream():
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/health')
def health():
    total    = DetectionEvent.objects.count()
    unsynced = DetectionEvent.objects.filter(synced_to_cloud=False).count()
    return {
        'status':   'ok',
        'camera':   camera_ok.is_set(),
        'local_db': {'total': total, 'pending_sync': unsynced},
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    t = threading.Thread(target=detection_loop, daemon=True)
    t.start()

    q = threading.Thread(target=flush_queue, daemon=True)
    q.start()

    d = threading.Thread(target=daily_digest_scheduler, daemon=True)
    d.start()

    print("Stream server running at http://localhost:5000/stream")
    print("Health check at     http://localhost:5000/health\n")
    app.run(host='0.0.0.0', port=5000, threaded=True)