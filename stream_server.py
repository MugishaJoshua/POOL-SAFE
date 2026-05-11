from flask import Flask, Response
from ultralytics import YOLO
import cv2
import requests
import time
import threading

app = Flask(__name__)

MODEL_PATH     = "best.pt"
DJANGO_URL     = "https://pool-safe-production.up.railway.app/api/ingest/"
CONFIDENCE     = 0.45
FRAME_INTERVAL = 2

SEVERITY_MAP = {
    "Animal":  "high",
    "Food":    "high",
    "Trash":   "medium",
    "Bottle":  "low",
}

# Fixed: was incorrectly mapping to severity values instead of class names
CLASS_MAP = {
    "Animal":  "animal",
    "Food":    "food",
    "Trash":   "trash",
    "Bottle":  "bottle",
}

# ── Camera helpers ────────────────────────────────────────────────────────────

CAMERA_SOURCE = 0   # change to RTSP URL string for IP cameras

def open_camera(source=CAMERA_SOURCE):
    """
    Try backends in order: DSHOW (most stable on Windows) → MSMF → any.
    Returns an opened VideoCapture or raises RuntimeError.
    """
    backends = [
        (cv2.CAP_DSHOW, "DirectShow"),
        (cv2.CAP_MSMF,  "MSMF"),
        (cv2.CAP_ANY,   "CAP_ANY"),
    ]

    # For RTSP / file paths, skip DSHOW
    if isinstance(source, str):
        backends = [(cv2.CAP_ANY, "CAP_ANY")]

    for backend, name in backends:
        print(f"  Trying {name} backend...", end=" ", flush=True)
        cap = cv2.VideoCapture(source, backend)
        if cap.isOpened():
            # Verify we can actually grab a frame
            ok, _ = cap.read()
            if ok:
                print(f"✅  ({name})")
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_FPS, 30)
                return cap
            cap.release()
            print("opened but no frames")
        else:
            cap.release()
            print("failed to open")

    raise RuntimeError(f"Cannot open camera source: {source}")


# ── Shared state ──────────────────────────────────────────────────────────────

last_sent     = {}
frame_lock    = threading.Lock()
current_frame = None
camera_ok     = threading.Event()   # set when camera is live

print("Loading model...")
model = YOLO(MODEL_PATH)
print(f"Model loaded ✅  |  Classes: {list(model.names.values())}")


# ── Detection / capture loop ──────────────────────────────────────────────────

def detection_loop():
    global current_frame

    while True:
        # ── Open camera (with retry) ──
        cap = None
        while cap is None:
            try:
                print("Opening camera...")
                cap = open_camera(CAMERA_SOURCE)
                camera_ok.set()
            except RuntimeError as e:
                print(f"❌  {e}  — retrying in 5 s...")
                camera_ok.clear()
                time.sleep(5)

        # ── Frame loop ──
        consecutive_failures = 0
        while True:
            ret, frame = cap.read()

            if not ret:
                consecutive_failures += 1
                if consecutive_failures >= 20:
                    print("⚠️  Too many consecutive frame failures — reopening camera...")
                    cap.release()
                    camera_ok.clear()
                    cap = None
                    break
                time.sleep(0.05)
                continue

            consecutive_failures = 0
            current_time = time.time()

            # Run YOLO
            results = model(frame, conf=CONFIDENCE, verbose=False)
            annotated = results[0].plot()

            with frame_lock:
                current_frame = annotated.copy()

            # Send detections to Django
            for result in results:
                for box in result.boxes:
                    class_id   = int(box.cls[0])
                    confidence = float(box.conf[0])
                    label      = model.names[class_id]
                    last_time  = last_sent.get(label, 0)

                    if current_time - last_time >= FRAME_INTERVAL:
                        object_class = CLASS_MAP.get(label, "trash")
                        severity     = SEVERITY_MAP.get(label, "low")
                        payload = {
                            "object_class":  object_class,
                            "confidence":    round(float(confidence), 4),
                            "location_note": f"CAM-01: {label}",
                        }
                        try:
                            r = requests.post(DJANGO_URL, json=payload, timeout=3)
                            status = "✅" if r.status_code == 201 else f"⚠️ {r.status_code}"
                            print(f"  {status}  {label} → {object_class}  {confidence:.0%}  [{severity}]")
                        except Exception as e:
                            print(f"  ❌  {e}")
                        last_sent[label] = current_time

            time.sleep(0.05)


# ── MJPEG stream generator ────────────────────────────────────────────────────

PLACEHOLDER = None   # lazy-built "camera offline" JPEG

def _make_placeholder():
    """Build a simple 'Camera Offline' placeholder frame."""
    import numpy as np
    img = np.zeros((480, 640, 3), dtype="uint8")
    img[:] = (30, 30, 30)
    cv2.putText(img, "Camera Offline", (160, 230),
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
    app.run(host='0.0.0.0', port=5000, threaded=True)