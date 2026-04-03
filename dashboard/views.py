import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Count
from datetime import timedelta
from .models import DetectionEvent, Notification

SEVERITY_MAP = {
    'animal': 'high',
    'littering': 'high',
    'trash': 'medium',
    'food': 'medium',
    'bottle': 'low',
}

CLASS_MESSAGES = {
    'trash':     'Trash detected near the pool. Please remove immediately.',
    'food':      'Food remains spotted at pool perimeter. Collect before attracting pests.',
    'animal':    'Animal intrusion detected! Escort animal away from pool area.',
    'bottle':    'Plastic bottle found near pool edge. Remove to prevent water contamination.',
    'littering': 'Littering behaviour observed. Approach visitor and request compliance.',
}


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

    confidence = float(data.get('confidence', 0.0))
    image_path = data.get('image_path', '')
    location_note = data.get('location_note', 'Pool Perimeter')
    severity = SEVERITY_MAP.get(obj_class, 'medium')

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
    notifications = Notification.objects.filter(id__gt=since_id, read=False).select_related('event')
    data = [
        {
            'id': n.id,
            'event_id': n.event.id,
            'object_class': n.event.object_class,
            'confidence': round(n.event.confidence * 100, 1),
            'severity': n.event.severity,
            'location': n.event.location_note,
            'message': n.message,
            'timestamp': n.event.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'image_path': n.event.image_path,
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
    page = int(request.GET.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page
    events = DetectionEvent.objects.all()[offset:offset + per_page]
    total = DetectionEvent.objects.count()
    data = [
        {
            'id': e.id,
            'object_class': e.object_class,
            'confidence': round(e.confidence * 100, 1),
            'severity': e.severity,
            'location': e.location_note,
            'timestamp': e.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'acknowledged': e.acknowledged,
            'image_path': e.image_path,
        }
        for e in events
    ]
    return JsonResponse({'events': data, 'total': total, 'page': page, 'per_page': per_page})


@require_http_methods(["GET"])
def stats(request):
    now = timezone.now()
    last_24h = now - timedelta(hours=24)

    by_class = list(
        DetectionEvent.objects.values('object_class').annotate(count=Count('id')).order_by('-count')
    )
    by_severity = list(
        DetectionEvent.objects.values('severity').annotate(count=Count('id'))
    )
    daily = []
    for i in range(7):
        day = (now - timedelta(days=6 - i)).date()
        count = DetectionEvent.objects.filter(timestamp__date=day).count()
        daily.append({'date': str(day), 'count': count})

    return JsonResponse({
        'by_class': by_class,
        'by_severity': by_severity,
        'daily': daily,
        'unread_notifications': Notification.objects.filter(read=False).count(),
        'total_today': DetectionEvent.objects.filter(timestamp__date=now.date()).count(),
        'total_24h': DetectionEvent.objects.filter(timestamp__gte=last_24h).count(),
        'total_all': DetectionEvent.objects.count(),
    })
