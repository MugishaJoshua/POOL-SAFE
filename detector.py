import cv2
import requests
import time
from ultralytics import YOLO

# ── Config ─────────────────────────────────────────────
MODEL_PATH     = "best.pt"
print("Model classes:", YOLO.names)  # ← add this
print("Model loaded ✅")
DJANGO_URL     = "https://pool-safe-production.up.railway.app/api/ingest/"
FRAME_INTERVAL = 10             # seconds before re-alerting the same class

# Detection threshold — lower so the model sees detections,
# then MIN_SEND_CONFIDENCE decides whether to report them.
CONFIDENCE     = 0.85

# Camera source — choose one:
# CAMERA_SOURCE = 0                                            # webcam
# CAMERA_SOURCE = "rtsp://user:pass@192.168.1.100:554/stream" # IP camera
CAMERA_SOURCE = 0

# ── Camera backend  ────────────────────────
CAP_BACKEND = cv2.CAP_DSHOW     # change to cv2.CAP_ANY if not on Windows

# ── Class config
SEVERITY_MAP = {
    "Animal":  "high",
    "Bottle":  "medium",
    "Food":    "medium",
    "Trash":   "low",
}

CLASS_MAP = {
    "Animal":  "animal",
    "Bottle":  "bottle",
    "Food":    "food",
    "Trash":   "trash",
}

# Per-class minimum confidence before sending to backend.
# Higher bar for Animal (human misfires) and Trash (model default bias).
MIN_SEND_CONFIDENCE = {
    "Animal":  0.80,
    "Bottle":  0.60,
    "Food":    0.60,
    "Trash":   0.75,
}

DEFAULT_SEVERITY     = "low"
DEFAULT_OBJECT_CLASS = "trash"

# ── Human filter ────────────────────────────────────────
# Loads YOLOv8n (tiny general model) to check if a detected region
# is actually a person before sending an "Animal" alert.
# Prevents humans from being reported as animals.
FILTER_HUMANS = True

human_model = None
if FILTER_HUMANS:
    try:
        human_model = YOLO("YOLOv8n.pt")   # downloads automatically (~6 MB)
        print("Human filter loaded ✅")
    except Exception as e:
        print(f"⚠️  Human filter unavailable ({e}) — continuing without it")
        human_model = None

def is_human(frame, x1, y1, x2, y2) -> bool:
    """Returns True if the cropped region contains a person."""
    if human_model is None:
        return False
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return False
    results = human_model(crop, verbose=False, conf=0.5)
    for r in results:
        for box in r.boxes:
            if int(box.cls[0]) == 0:   # class 0 = person in COCO
                return True
    return False

# ── Load PoolGuard model ─────────────────────────────────
print("Loading PoolGuard model...")
model = YOLO(MODEL_PATH)
print(f"Model loaded ✅  |  Classes: {list(model.names.values())}")

# ── Start camera ────────────────────────────────────────
def open_camera(source, backend=CAP_BACKEND, retries=5):
    for attempt in range(1, retries + 1):
        cap = cv2.VideoCapture(source, backend)
        if cap.isOpened():
            print(f"Camera opened ✅ (backend={'DSHOW' if backend == cv2.CAP_DSHOW else 'default'})")
            return cap
        cap.release()
        if attempt == 1 and backend != cv2.CAP_ANY:
            print("DSHOW failed, retrying with default backend...")
            backend = cv2.CAP_ANY
        else:
            print(f"Camera open attempt {attempt}/{retries} failed, retrying in 2 s...")
            time.sleep(2)
    raise RuntimeError(f"Cannot open camera source after {retries} attempts: {source}")

cap = open_camera(CAMERA_SOURCE)
print("Watching for threats...\n")

last_sent = {}  # {class_name: timestamp}


def send_detection(label: str, confidence: float, camera_id: str = "CAM-01"):
    severity     = SEVERITY_MAP.get(label, DEFAULT_SEVERITY)
    object_class = CLASS_MAP.get(label, DEFAULT_OBJECT_CLASS)
    payload = {
        "object_class":  object_class,
        "confidence":    round(float(confidence), 4),
        "location_note": f"{camera_id}: {label}",
    }
    try:
        response = requests.post(DJANGO_URL, json=payload, timeout=3)
        if response.status_code == 201:
            print(f"  ✅ Sent: {label} → {object_class} ({confidence:.0%}) [{severity}]")
        else:
            print(f"  ⚠️  Server responded {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Failed to send detection: {e}")


# ── Main loop ───────────────────────────────────────────
try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("⚠️  Lost camera feed — attempting reconnect...")
            cap.release()
            time.sleep(2)
            try:
                cap = open_camera(CAMERA_SOURCE)
            except RuntimeError as e:
                print(f"❌ {e}")
                break
            continue

        current_time = time.time()
        results = model(frame, conf=CONFIDENCE, iou=0.45, imgsz=640, verbose=False)

        for result in results:
            for box in result.boxes:
                class_id   = int(box.cls[0])
                confidence = float(box.conf[0])
                label      = model.names[class_id]

                # ── Filter 1: per-class confidence gate ──
                min_conf = MIN_SEND_CONFIDENCE.get(label, 0.60)
                if confidence < min_conf:
                    print(f"  [SKIP] {label} {confidence:.0%} < {min_conf:.0%} threshold")
                    continue

                # ── Filter 2: human check (Animal class only) ──
                if label == "Animal" and FILTER_HUMANS:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    if is_human(frame, x1, y1, x2, y2):
                        print(f"  [SKIP] Animal detection overridden — region contains a person")
                        continue

                # ── Cooldown check ──
                if current_time - last_sent.get(label, 0) < FRAME_INTERVAL:
                    continue

                send_detection(label, confidence)
                last_sent[label] = current_time

        # Optional live preview — uncomment to enable
        # annotated = results[0].plot()
        # cv2.imshow("PoolGuard Live Detection", annotated)
        # if cv2.waitKey(1) & 0xFF == ord('q'):
        #     print("Stopping detector...")
        #     break

        time.sleep(0.1)

except KeyboardInterrupt:
    print("\nDetector stopped.")

finally:
    cap.release()
    cv2.destroyAllWindows()
