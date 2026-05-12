import cv2
import requests
import time
from ultralytics import YOLO

# ── Config ─────────────────────────────────────────────
MODEL_PATH     = "best.pt"
DJANGO_URL     = "https://pool-safe-production.up.railway.app/api/ingest/"
FRAME_INTERVAL = 10

CONFIDENCE     = 0.45          # YOLO pre-filter (keep low, we gate below)
CAMERA_SOURCE  = 0
CAP_BACKEND    = cv2.CAP_DSHOW

# ── NEW: Two-tier threshold ──────────────────────────────
DETECT_THRESHOLD = 0.80        # print to terminal if ≥ this
ALERT_THRESHOLD  = 0.90        # send to Django if ≥ this

# ── Class config ────────────────────────────────────────
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

THREAT_CLASSES = set(CLASS_MAP.keys())   # {"Animal", "Bottle", "Food", "Trash"}

# ── Human filter ────────────────────────────────────────
FILTER_HUMANS = True
human_model = None
if FILTER_HUMANS:
    try:
        human_model = YOLO("yolov8n.pt")
        print("Human filter loaded ✅")
    except Exception as e:
        print(f"⚠️  Human filter unavailable ({e}) — continuing without it")

def is_human(frame, x1, y1, x2, y2) -> bool:
    if human_model is None:
        return False
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return False
    results = human_model(crop, verbose=False, conf=0.5)
    for r in results:
        for box in r.boxes:
            if int(box.cls[0]) == 0:
                return True
    return False

# ── Load PoolGuard model ─────────────────────────────────
print("Loading PoolGuard model...")
model = YOLO(MODEL_PATH)
print(f"Model loaded ✅  |  Classes: {list(model.names.values())}")

# ── Camera ──────────────────────────────────────────────
def open_camera(source, backend=CAP_BACKEND, retries=5):
    for attempt in range(1, retries + 1):
        cap = cv2.VideoCapture(source, backend)
        if cap.isOpened():
            print(f"Camera opened ✅")
            return cap
        cap.release()
        if attempt == 1 and backend != cv2.CAP_ANY:
            print("DSHOW failed, retrying with default backend...")
            backend = cv2.CAP_ANY
        else:
            print(f"Attempt {attempt}/{retries} failed, retrying in 2s...")
            time.sleep(2)
    raise RuntimeError(f"Cannot open camera after {retries} attempts: {source}")

cap = open_camera(CAMERA_SOURCE)
print(f"\nWatching for threats... (detect ≥ {DETECT_THRESHOLD:.0%} | alert ≥ {ALERT_THRESHOLD:.0%})\n")

last_sent = {}


def send_detection(label: str, confidence: float, camera_id: str = "CAM-01"):
    severity     = SEVERITY_MAP[label]
    object_class = CLASS_MAP[label]
    payload = {
        "object_class":  object_class,
        "confidence":    round(confidence, 4),
        "location_note": f"{camera_id}: {label}",
    }
    try:
        response = requests.post(DJANGO_URL, json=payload, timeout=3)
        if response.status_code == 201:
            print(f"  🚨 ALERT SENT: {label} → {object_class}  {confidence:.0%}  [{severity}]")
        else:
            print(f"  ⚠️  Server {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Send failed: {e}")


# ── Main loop ───────────────────────────────────────────
try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("⚠️  Lost feed — reconnecting...")
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

                # ── Only process threat classes ──────────────────────
                if label not in THREAT_CLASSES:
                    continue

                # ── Gate 1: must meet detection threshold to even print
                if confidence < DETECT_THRESHOLD:
                    continue

                severity = SEVERITY_MAP[label]
                print(f"  👁  Detected: {label}  {confidence:.0%}  [{severity}]", end="")

                # ── Human override (Animal class only) ───────────────
                if label == "Animal" and FILTER_HUMANS:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    if is_human(frame, x1, y1, x2, y2):
                        print("  → skipped (human)")
                        continue

                # ── Gate 2: must meet alert threshold to send ────────
                if confidence < ALERT_THRESHOLD:
                    print("  → detected, below alert threshold")
                    continue

                # ── Cooldown: don't flood the backend ────────────────
                if current_time - last_sent.get(label, 0) < FRAME_INTERVAL:
                    print("  → cooldown active")
                    continue

                print()   # newline before the alert line
                send_detection(label, confidence)
                last_sent[label] = current_time

        time.sleep(0.1)

except KeyboardInterrupt:
    print("\nDetector stopped.")

finally:
    cap.release()
    cv2.destroyAllWindows()