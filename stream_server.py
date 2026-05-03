from flask import Flask, Response
from ultralytics import YOLO
import cv2
import requests
import time
import threading

app = Flask(__name__)

MODEL_PATH   = "best.pt"
DJANGO_URL   = "https://pool-safe-production.up.railway.app/api/ingest/"
CONFIDENCE   = 0.45
FRAME_INTERVAL = 2

SEVERITY_MAP = {
    "Bird": "high", "Cat": "high", "Dog": "high",
    "Food waste": "medium", "Garbage bag": "high",
    "Clear plastic bottle": "medium", "Other plastic bottle": "medium",
    "Plastic film": "low", "Plastic straw": "low",
    "Cigarette": "medium", "Drink can": "low",
    "Unlabeled litter": "medium",
}

CLASS_MAP = {
    "Bird": "animal", "Cat": "animal", "Dog": "animal",
    "Food waste": "food", "Garbage bag": "trash",
    "Clear plastic bottle": "bottle", "Other plastic bottle": "bottle",
    "Plastic film": "trash", "Plastic straw": "trash",
    "Cigarette": "littering", "Drink can": "trash",
    "Unlabeled litter": "trash",
}

print("Loading model...")
model = YOLO(MODEL_PATH)
print("Model loaded ✅")

cap = cv2.VideoCapture(0)
last_sent = {}
frame_lock = threading.Lock()
current_frame = None

def detection_loop():
    global current_frame
    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        current_time = time.time()
        results = model(frame, conf=CONFIDENCE, verbose=False)
        annotated = results[0].plot()

        with frame_lock:
            current_frame = annotated.copy()

        for result in results:
            for box in result.boxes:
                class_id   = int(box.cls[0])
                confidence = float(box.conf[0])
                label      = model.names[class_id]
                last_time  = last_sent.get(label, 0)
                if current_time - last_time >= FRAME_INTERVAL:
                    object_class = CLASS_MAP.get(label, "trash")
                    payload = {
                        "object_class":  object_class,
                        "confidence":    round(float(confidence), 4),
                        "location_note": f"CAM-01: {label}",
                    }
                    try:
                        requests.post(DJANGO_URL, json=payload, timeout=3)
                        print(f"✅ {label} → {object_class} ({confidence:.2f})")
                    except Exception as e:
                        print(f"❌ {e}")
                    last_sent[label] = current_time

        time.sleep(0.05)

def generate():
    while True:
        with frame_lock:
            frame = current_frame
        if frame is None:
            time.sleep(0.1)
            continue
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        time.sleep(0.033)  # ~30fps

@app.route('/stream')
def stream():
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/health')
def health():
    return 'ok'

if __name__ == '__main__':
    t = threading.Thread(target=detection_loop, daemon=True)
    t.start()
    print("Stream server running at http://localhost:5000/stream")
    app.run(host='0.0.0.0', port=5000, threaded=True)