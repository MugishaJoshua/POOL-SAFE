import cv2
import requests
import time
from ultralytics import YOLO

# ── Config ─────────────────────────────────────────────
MODEL_PATH     = "best.pt"
DJANGO_URL     = "https://pool-safe-production.up.railway.app/api/ingest/"
CONFIDENCE     = 0.45       # minimum confidence threshold
FRAME_INTERVAL = 2          # process every N seconds (avoid spamming)

# Camera source — choose one:
# CAMERA_SOURCE = 0                        # webcam (default)
# CAMERA_SOURCE = "rtsp://user:pass@192.168.1.100:554/stream"  # IP camera
# For webcam:
CAMERA_SOURCE = 0

# Severity mapping by class name
SEVERITY_MAP = {
    "Bird":                   "high",
    "Cat":                    "high",
    "Dog":                    "high",
    "Food waste":             "medium",
    "Garbage bag":            "high",
    "Clear plastic bottle":   "medium",
    "Other plastic bottle":   "medium",
    "Plastic film":           "low",
    "Plastic straw":          "low",
    "Cigarette":              "medium",
    "Drink can":              "low",
    "Unlabeled litter":       "medium",
}
DEFAULT_SEVERITY = "low"

# ── Load model ──────────────────────────────────────────
print("Loading PoolGuard model...")
model = YOLO(MODEL_PATH)
print("Model loaded ✅")

# ── Start camera ────────────────────────────────────────
cap = cv2.VideoCapture(CAMERA_SOURCE)
if not cap.isOpened():
    raise RuntimeError(f"Cannot open camera source: {CAMERA_SOURCE}")

print(f"Camera started. Watching for threats...")

last_sent = {}   # track last time each class was sent to avoid duplicate alerts

def send_detection(label, confidence, camera_id="CAM-01"):
    severity = SEVERITY_MAP.get(label, DEFAULT_SEVERITY)
    payload = {
        "camera_id":    camera_id,
        "label":        label,
        "confidence":   round(float(confidence), 4),
        "severity":     severity,
    }
    try:
        response = requests.post(DJANGO_URL, json=payload, timeout=3)
        if response.status_code == 201:
            print(f"  ✅ Sent: {label} ({confidence:.2f}) [{severity}]")
        else:
            print(f"  ⚠️  Server responded {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Failed to send detection: {e}")

# ── Main loop ───────────────────────────────────────────
try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame, retrying...")
            time.sleep(1)
            continue

        current_time = time.time()

        # Run YOLOv8 inference
        results = model(frame, conf=CONFIDENCE, verbose=False)

        for result in results:
            for box in result.boxes:
                class_id   = int(box.cls[0])
                confidence = float(box.conf[0])
                label      = model.names[class_id]

                # Only send if enough time has passed since last alert for this class
                last_time = last_sent.get(label, 0)
                if current_time - last_time >= FRAME_INTERVAL:
                    send_detection(label, confidence)
                    last_sent[label] = current_time

        # Show live feed with bounding boxes (optional — comment out if headless)
        #annotated = results[0].plot()
        ##cv2.imshow("PoolGuard Live Detection", annotated)

        ##if cv2.waitKey(1) & 0xFF == ord('q'):
         #   print("Stopping detector...")
          #  break
        time.sleep(0.1)  

except KeyboardInterrupt:
    print("\nDetector stopped.")


finally:
    cap.release()
    cv2.destroyAllWindows()