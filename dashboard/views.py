import json
import threading
import time
from datetime import timedelta

from django.db.models import Count
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import DetectionEvent, Notification

# ── Constants ────────────────────────────────────────────────────────────────

# Keys must match model.names values (Title-case from YOLOv8 training)
SEVERITY_MAP = {
    'Animal':    'high',
    'Littering': 'high',
    'Trash':     'medium',
    'Food':      'medium',
    'Bottle':    'low',
}

CLASS_MESSAGES = {
    'trash':     'Trash detected near the pool. Please remove immediately.',
    'food':      'Food remains spotted at pool perimeter. Collect before attracting pests.',
    'animal':    'Animal intrusion detected! Escort animal away from pool area.',
    'bottle':    'Plastic bottle found near pool edge. Remove to prevent water contamination.',
    'littering': 'Littering behaviors observed. Approach visitor and request compliance.',
}

# ── Lazy YOLO loader (only used by /video-feed/) ─────────────────────────────

_model = None
_model_lock = threading.Lock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            import os
            from ultralytics import YOLO
            model_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'best.pt'
            )
            _model = YOLO(model_path)
    return _model


# ── Views ────────────────────────────────────────────────────────────────────

def dashboard(request):
    return render(request, 'dashboard/index.html')


@csrf_exempt
@require_http_methods(["POST"])
def ingest_detection(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    obj_class = data.get('object_class', '').lower()
    valid_classes = [c[0] for c in DetectionEvent._meta.get_field('object_class').choices]
    if obj_class not in valid_classes:
        return JsonResponse({'error': f'Unknown object_class: {obj_class}'}, status=400)

    confidence    = float(data.get('confidence', 0.0))
    image_path    = data.get('image_path', '')
    location_note = data.get('location_note', 'Pool Perimeter')

    # SEVERITY_MAP uses Title-case keys; obj_class is lowercase here
    severity = SEVERITY_MAP.get(obj_class.title(), 'medium')

    event = DetectionEvent.objects.create(
        object_class=obj_class,
        confidence=confidence,
        image_path=image_path,
        severity=severity,
        location_note=location_note,
    )

    message = CLASS_MESSAGES.get(obj_class, f'{obj_class.title()} detected at pool.')
    Notification.objects.create(event=event, message=message)

    return JsonResponse({'status': 'ok', 'event_id': event.id}, status=201)


@require_http_methods(["GET"])
def poll_notifications(request):
    since_id = int(request.GET.get('since_id', 0))
    notifications = (
        Notification.objects
        .filter(id__gt=since_id, read=False)
        .select_related('event')
    )
    data = [
        {
            'id':           n.id,
            'event_id':     n.event.id,
            'object_class': n.event.object_class,
            'confidence':   round(n.event.confidence * 100, 1),
            'severity':     n.event.severity,
            'location':     n.event.location_note,
            'message':      n.message,
            'timestamp':    n.event.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'image_path':   n.event.image_path,
        }
        for n in notifications
    ]
    return JsonResponse({'notifications': data})


@csrf_exempt
@require_http_methods(["POST"])
def mark_read(request, notification_id):
    Notification.objects.filter(id=notification_id).update(read=True)
    return JsonResponse({'status': 'ok'})


@csrf_exempt
@require_http_methods(["POST"])
def acknowledge_event(request, event_id):
    DetectionEvent.objects.filter(id=event_id).update(acknowledged=True)
    Notification.objects.filter(event_id=event_id).update(read=True)
    return JsonResponse({'status': 'ok'})


@require_http_methods(["GET"])
def history(request):
    page     = int(request.GET.get('page', 1))
    per_page = 20
    offset   = (page - 1) * per_page
    events   = DetectionEvent.objects.all()[offset:offset + per_page]
    total    = DetectionEvent.objects.count()
    data = [
        {
            'id':           e.id,
            'object_class': e.object_class,
            'confidence':   round(e.confidence * 100, 1),
            'severity':     e.severity,
            'location':     e.location_note,
            'timestamp':    e.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'acknowledged': e.acknowledged,
            'image_path':   e.image_path,
        }
        for e in events
    ]
    return JsonResponse({'events': data, 'total': total, 'page': page, 'per_page': per_page})


@require_http_methods(["GET"])
def stats(request):
    now      = timezone.now()
    last_24h = now - timedelta(hours=24)

    by_class = list(
        DetectionEvent.objects
        .values('object_class')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    by_severity = list(
        DetectionEvent.objects.values('severity').annotate(count=Count('id'))
    )
    daily = [
        {
            'date':  str((now - timedelta(days=6 - i)).date()),
            'count': DetectionEvent.objects.filter(
                timestamp__date=(now - timedelta(days=6 - i)).date()
            ).count(),
        }
        for i in range(7)
    ]

    return JsonResponse({
        'by_class':             by_class,
        'by_severity':          by_severity,
        'daily':                daily,
        'unread_notifications': Notification.objects.filter(read=False).count(),
        'total_today':          DetectionEvent.objects.filter(timestamp__date=now.date()).count(),
        'total_24h':            DetectionEvent.objects.filter(timestamp__gte=last_24h).count(),
        'total_all':            DetectionEvent.objects.count(),
    })


# ── Live video feed ───────────────────────────────────────────────────────────

# Cooldown: don't save the same class more than once every N seconds per feed
_feed_last_sent = {}
_FEED_COOLDOWN  = 10  # seconds


def generate_frames(source=0):
    import cv2
    model = get_model()
    cap   = cv2.VideoCapture(source)

    if not cap.isOpened():
        return

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            results   = model(frame, conf=0.45, verbose=False)
            annotated = results[0].plot()

            now = time.time()
            for result in results:
                for box in result.boxes:
                    class_id   = int(box.cls[0])
                    confidence = float(box.conf[0])
                    label      = model.names[class_id]   # Title-case e.g. "Animal"
                    label_lc   = label.lower()

                    # Cooldown — skip if this class was recently saved
                    last = _feed_last_sent.get(label, 0)
                    if now - last < _FEED_COOLDOWN:
                        continue
                    _feed_last_sent[label] = now

                    severity = SEVERITY_MAP.get(label, 'low')
                    message  = CLASS_MESSAGES.get(label_lc, f'{label} detected at pool.')

                    event = DetectionEvent.objects.create(
                        object_class=label_lc,
                        confidence=confidence,
                        severity=severity,
                        location_note='Live Camera',
                    )
                    Notification.objects.create(event=event, message=message)

            _, buffer   = cv2.imencode('.jpg', annotated)
            frame_bytes = buffer.tobytes()
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n'
            )
    finally:
        cap.release()


@require_http_methods(["GET"])
def video_feed(request):
    source = request.GET.get('source', '0')
    try:
        source = int(source)
    except ValueError:
        pass  # keep as string for RTSP URLs

    return StreamingHttpResponse(
        generate_frames(source),
        content_type='multipart/x-mixed-replace; boundary=frame'
    )