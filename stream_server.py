from flask import Flask, Response
from ultralytics import YOLO
import cv2
import requests
import time
import threading
import numpy as np

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_PATH     = "best.pt"
DJANGO_URL = "https://pool-guard.onrender.com/api/ingest/"
CONFIDENCE     = 0.40          # YOLO pre-filter — kept low, gated below
FRAME_INTERVAL = 2             # seconds between alerts for the same class

DETECT_THRESHOLD = 0.80        # print to terminal if confidence ≥ this
ALERT_THRESHOLD  = 0.80        # send to Django (trigger alert) if confidence ≥ this

CAMERA_SOURCE  = 0             # 0 = first webcam; swap for RTSP string if needed

# ── Class config ──────────────────────────────────────────────────────────────
SEVERITY_MAP = {
    "Animal":  "high",
    "Food":    "high",
    "Trash":   "medium",
    "Bottle":  "low",
}

CLASS_MAP = {
    "Animal":  "animal",
    "Food":    "food",
    "Trash":   "trash",
    "Bottle":  "bottle",
}

THREAT_CLASSES = set(CLASS_MAP.keys())   # only these are ever alerted on

# ── Camera helpers ────────────────────────────────────────────────────────────

def open_camera(source=CAMERA_SOURCE):
    """
    For integer (webcam) sources: try DSHOW → MSMF → CAP_ANY.
    For string (RTSP/HTTP) sources: try CAP_FFMPEG → CAP_ANY.
    Returns an opened VideoCapture or raises RuntimeError.
    """
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

        # Verify we can actually grab a frame
        ok, _ = cap.read()
        if not ok:
            cap.release()
            print("opened but no frames — skipping")
            continue

        # Set resolution and FPS
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
        # ── Open camera (with retry) ──────────────────────────────────────────
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

        # ── Frame loop ────────────────────────────────────────────────────────
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

            # ── Run YOLO ─────────────────────────────────────────────────────
            results = model(frame, conf=CONFIDENCE, iou=0.45, imgsz=640, verbose=False)
            annotated = results[0].plot()

            with frame_lock:
                current_frame = annotated.copy()

            # ── Process detections ────────────────────────────────────────────
            for result in results:
                for box in result.boxes:
                    class_id   = int(box.cls[0])
                    confidence = float(box.conf[0])
                    label      = model.names[class_id]

                    # Only process known threat classes — silently skip everything else
                    if label not in THREAT_CLASSES:
                        continue

                    # Gate 1: must meet detection threshold to even appear in terminal
                    if confidence < DETECT_THRESHOLD:
                        continue

                    severity     = SEVERITY_MAP[label]
                    object_class = CLASS_MAP[label]

                    print(f"  👁  {label} → {object_class}  {confidence:.0%}  [{severity}]", end="")

                    # Gate 2: must meet alert threshold to send to Django
                    if confidence < ALERT_THRESHOLD:
                        print("  (detected — below alert threshold)")
                        continue

                    # Cooldown: don't flood the backend with the same class
                    if current_time - last_sent.get(label, 0) < FRAME_INTERVAL:
                        print("  (cooldown)")
                        continue

                    # ── Send alert ────────────────────────────────────────────
                    payload = {
                        "object_class":  object_class,
                        "confidence":    round(confidence, 4),
                        "location_note": f"CAM-01: {label}",
                        "status":        "active",
                        "severity":      severity,
                    }
                    try:
                        r = requests.post(DJANGO_URL, json=payload, timeout=10)
                        if r.status_code == 201:
                            print(f"\n  🚨 ALERT SENT: {label} → {object_class}  {confidence:.0%}  [{severity}]")
                        else:
                            error_detail = r.json().get('error', '?') if 'json' in r.headers.get('Content-Type', '') else f"HTTP {r.status_code}"
                            print(f"\n  ⚠️  Alert failed: {error_detail}")
                    except Exception as e:
                        print(f"\n  ❌  Send failed: {e}")

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
        time.sleep(0.033)   # ~30 fps


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route('/stream')
def stream():
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/health')
def health():
    return {'status': 'ok', 'camera': camera_ok.is_set()}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    t = threading.Thread(target=detection_loop, daemon=True)
    t.start()
    print("Stream server running at http://localhost:5000/stream")
    print("Health check at     http://localhost:5000/health\n")
    app.run(host='0.0.0.0', port=5000, threaded=True)