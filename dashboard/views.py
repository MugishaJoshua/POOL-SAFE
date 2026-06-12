import io
import json
import threading
import time
from datetime import timedelta
from django.core.mail import send_mail
from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Count
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Image as RLImage, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .models import DetectionEvent, Notification, AlertRecipient

# ── Constants ────────────────────────────────────────────────────────────────

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

# ── Lazy YOLO loader ─────────────────────────────────────────────────────────

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


# ── Dashboard ─────────────────────────────────────────────────────────────────

def dashboard(request):
    return render(request, 'dashboard/index.html')

def send_alert_email(event, message):
    try:
        recipients = list(
            AlertRecipient.objects.filter(is_active=True).values_list('email', flat=True)
        )
        if not recipients:
            return
        send_mail(
            subject=f'[PoolGuard] {event.severity.upper()} Alert — {event.object_class.title()} Detected',
            message=(
                f'PoolGuard has detected a hazard at the pool.\n\n'
                f'Object:     {event.object_class.title()}\n'
                f'Confidence: {event.confidence:.1%}\n'
                f'Severity:   {event.severity.upper()}\n'
                f'Location:   {event.location_note}\n'
                f'Time:       {event.timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n\n'
                f'{message}\n\n'
                f'Log in to the dashboard for details:\n'
                f'https://pool-guard.onrender.com'
            ),
            from_email=settings.EMAIL_HOST_USER,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception as e:
        print(f'Email error: {e}')
# ── Ingest (now accepts multipart OR JSON) ────────────────────────────────────


@csrf_exempt
@require_http_methods(["POST"])
def ingest_detection(request):
    """
    Accepts detection data from the YOLOv8 detector.

    Supports two content types:
      • multipart/form-data  — detector sends images as file uploads
      • application/json     — legacy / no-image mode

    Multipart fields:
      object_class, confidence, location_note   (text)
      full_frame       (file, optional)
      cropped_object   (file, optional)

    JSON body fields (legacy):
      object_class, confidence, image_path, location_note
    """
    content_type = request.content_type or ''

    if 'multipart' in content_type:
        # ── Multipart (with images) ───────────────────────────────────────
        data = request.POST
        obj_class    = data.get('object_class', '').lower()
        confidence   = float(data.get('confidence', 0.0))
        location_note = data.get('location_note', 'Pool Perimeter')
        image_path   = ''  # no longer needed; kept for compat
    else:
        # ── JSON (legacy / no-image) ──────────────────────────────────────
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        obj_class    = data.get('object_class', '').lower()
        confidence   = float(data.get('confidence', 0.0))
        location_note = data.get('location_note', 'Pool Perimeter')
        image_path   = data.get('image_path', '')

    # Validate class
    valid_classes = [c[0] for c in DetectionEvent._meta.get_field('object_class').choices]
    if obj_class not in valid_classes:
        return JsonResponse({'error': f'Unknown object_class: {obj_class}'}, status=400)

    severity = SEVERITY_MAP.get(obj_class.title(), 'medium')

    event = DetectionEvent(
        object_class=obj_class,
        confidence=confidence,
        image_path=image_path,
        severity=severity,
        location_note=location_note,
    )

    # Save image files if present
    if 'full_frame' in request.FILES:
        event.full_frame.save(
            f'full_frame_{timezone.now().strftime("%Y%m%d_%H%M%S%f")}.jpg',
            request.FILES['full_frame'],
            save=False,
        )

    if 'cropped_object' in request.FILES:
        event.cropped_object.save(
            f'crop_{timezone.now().strftime("%Y%m%d_%H%M%S%f")}.jpg',
            request.FILES['cropped_object'],
            save=False,
        )

    event.save()

    message = CLASS_MESSAGES.get(obj_class, f'{obj_class.title()} detected at pool.')
    Notification.objects.create(event=event, message=message)
    send_alert_email(event, message) 
    return JsonResponse({'status': 'ok', 'event_id': event.id}, status=201)


# ── Notifications ─────────────────────────────────────────────────────────────

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
            'full_frame_url':     n.event.full_frame.url if n.event.full_frame else None,
            'cropped_object_url': n.event.cropped_object.url if n.event.cropped_object else None,
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


# ── History ───────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def history(request):
    page     = int(request.GET.get('page', 1))
    per_page = 20
    offset   = (page - 1) * per_page

    qs = DetectionEvent.objects.all()

    obj_class = request.GET.get('object_class')
    severity  = request.GET.get('severity')
    status    = request.GET.get('status')
    date_from = request.GET.get('date_from')
    date_to   = request.GET.get('date_to')

    if obj_class: qs = qs.filter(object_class=obj_class)
    if severity:  qs = qs.filter(severity=severity)
    if status == 'active':  qs = qs.filter(acknowledged=False)
    if status == 'cleared': qs = qs.filter(acknowledged=True)
    if date_from: qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:   qs = qs.filter(timestamp__date__lte=date_to)

    total  = qs.count()
    events = qs[offset:offset + per_page]

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
            'full_frame_url':     e.full_frame.url if e.full_frame else None,
            'cropped_object_url': e.cropped_object.url if e.cropped_object else None,
        }
        for e in events
    ]
    return JsonResponse({'events': data, 'total': total, 'page': page, 'per_page': per_page})

# ── Stats ─────────────────────────────────────────────────────────────────────

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


# ── PDF: single event ─────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def download_event_pdf(request, event_id):
    try:
        event = DetectionEvent.objects.get(id=event_id)
    except DetectionEvent.DoesNotExist:
        return JsonResponse({'error': 'Event not found'}, status=404)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=50, leftMargin=50, topMargin=60, bottomMargin=50,
    )
    styles = getSampleStyleSheet()
    blue   = colors.HexColor('#1a73e8')
    grey   = colors.HexColor('#dee2e6')
    light  = colors.HexColor('#f8f9fa')

    title_style = ParagraphStyle('PGTitle', parent=styles['Title'],
                                 fontSize=22, textColor=blue, spaceAfter=6)
    h2_style    = ParagraphStyle('PGH2', parent=styles['Heading2'],
                                 fontSize=13, textColor=colors.HexColor('#333333'),
                                 spaceBefore=14, spaceAfter=6)
    footer_style = ParagraphStyle('PGFooter', parent=styles['Normal'],
                                  fontSize=8, textColor=colors.grey, alignment=TA_CENTER)

    story = []

    # Header
    story.append(Paragraph("PoolGuard — Detection Report", title_style))
    story.append(Paragraph(f"Event ID: #{event.id}", styles['Normal']))
    story.append(HRFlowable(width="100%", thickness=1, color=blue))
    story.append(Spacer(1, 14))

    # Details table
    details = [
        ['Field', 'Value'],
        ['Detected Class', event.object_class.title()],
        ['Confidence',     f"{event.confidence:.1%}"],
        ['Severity',       event.severity.upper()],
        ['Location',       event.location_note],
        ['Timestamp',      event.timestamp.strftime('%Y-%m-%d %H:%M:%S')],
        ['Acknowledged',   'Yes' if event.acknowledged else 'No'],
    ]
    tbl = Table(details, colWidths=[2 * inch, 4 * inch])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0), blue),
        ('TEXTCOLOR',    (0, 0), (-1, 0), colors.white),
        ('FONTNAME',     (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, 0), 11),
        ('FONTNAME',     (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE',     (0, 1), (-1, -1), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [light, colors.white]),
        ('GRID',         (0, 0), (-1, -1), 0.5, grey),
        ('PADDING',      (0, 0), (-1, -1), 8),
        ('ALIGN',        (0, 0), (-1, -1), 'LEFT'),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 20))

    # Images
    if event.full_frame or event.cropped_object:
        story.append(Paragraph("Captured Images", h2_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=grey))
        story.append(Spacer(1, 10))

    if event.full_frame:
        try:
            story.append(Paragraph("Full Frame", styles['Heading3']))
            story.append(RLImage(event.full_frame.path, width=5 * inch, height=3.5 * inch, kind='proportional'))
            story.append(Spacer(1, 12))
        except Exception:
            story.append(Paragraph("Full frame image unavailable.", styles['Normal']))

    if event.cropped_object:
        try:
            story.append(Paragraph("Cropped Detection", styles['Heading3']))
            story.append(RLImage(event.cropped_object.path, width=3 * inch, height=3 * inch, kind='proportional'))
            story.append(Spacer(1, 12))
        except Exception:
            story.append(Paragraph("Cropped image unavailable.", styles['Normal']))

    # Footer
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=grey))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Generated by PoolGuard on {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
        footer_style,
    ))

    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="poolguard_event_{event.id}.pdf"'
    return response


# ── PDF: full report ──────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def download_full_report_pdf(request):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=50, leftMargin=50, topMargin=60, bottomMargin=50,
    )
    styles = getSampleStyleSheet()
    blue  = colors.HexColor('#1a73e8')
    grey  = colors.HexColor('#dee2e6')
    light = colors.HexColor('#f8f9fa')
    now   = timezone.now()

    title_style = ParagraphStyle('PGTitle', parent=styles['Title'],
                                 fontSize=24, textColor=blue, alignment=TA_CENTER, spaceAfter=8)
    sub_style   = ParagraphStyle('PGSub', parent=styles['Normal'],
                                 fontSize=12, textColor=colors.grey, alignment=TA_CENTER, spaceAfter=20)
    h2_style    = ParagraphStyle('PGH2', parent=styles['Heading2'],
                                 fontSize=14, textColor=blue, spaceBefore=20, spaceAfter=8)
    footer_style = ParagraphStyle('PGFooter', parent=styles['Normal'],
                                  fontSize=8, textColor=colors.grey, alignment=TA_CENTER)

    events      = DetectionEvent.objects.all()
    total       = events.count()
    unack       = events.filter(acknowledged=False).count()
    high_count  = events.filter(severity='high').count()
    med_count   = events.filter(severity='medium').count()
    low_count   = events.filter(severity='low').count()

    story = []

    # ── Cover ──
    story.append(Spacer(1, 60))
    story.append(Paragraph("PoolGuard", title_style))
    story.append(Paragraph("AI-Powered Pool Surveillance Report", sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=blue))
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"Generated: {now.strftime('%B %d, %Y at %H:%M:%S')}", sub_style))
    story.append(PageBreak())

    # ── Summary stats ──
    story.append(Paragraph("Summary Statistics", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=grey))
    story.append(Spacer(1, 10))

    stats_data = [
        ['Metric', 'Value'],
        ['Total Detections',   str(total)],
        ['Unacknowledged',     str(unack)],
        ['High Severity',      str(high_count)],
        ['Medium Severity',    str(med_count)],
        ['Low Severity',       str(low_count)],
    ]
    stats_tbl = Table(stats_data, colWidths=[3 * inch, 3 * inch])
    stats_tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0), blue),
        ('TEXTCOLOR',    (0, 0), (-1, 0), colors.white),
        ('FONTNAME',     (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, -1), 10),
        ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [light, colors.white]),
        ('GRID',         (0, 0), (-1, -1), 0.5, grey),
        ('PADDING',      (0, 0), (-1, -1), 10),
    ]))
    story.append(stats_tbl)
    story.append(Spacer(1, 20))

    # ── Class breakdown ──
    by_class = list(events.values('object_class').annotate(count=Count('id')).order_by('-count'))
    if by_class:
        story.append(Paragraph("Detection Class Breakdown", h2_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=grey))
        story.append(Spacer(1, 10))
        class_data = [['Class', 'Count', 'Percentage']]
        for item in by_class:
            pct = (item['count'] / total * 100) if total else 0
            class_data.append([item['object_class'].title(), str(item['count']), f"{pct:.1f}%"])
        class_tbl = Table(class_data, colWidths=[3 * inch, 1.5 * inch, 1.5 * inch])
        class_tbl.setStyle(TableStyle([
            ('BACKGROUND',   (0, 0), (-1, 0), blue),
            ('TEXTCOLOR',    (0, 0), (-1, 0), colors.white),
            ('FONTNAME',     (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',     (0, 0), (-1, -1), 10),
            ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [light, colors.white]),
            ('GRID',         (0, 0), (-1, -1), 0.5, grey),
            ('PADDING',      (0, 0), (-1, -1), 8),
        ]))
        story.append(class_tbl)
        story.append(PageBreak())

    # ── All events table ──
    story.append(Paragraph("All Detection Events", h2_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=grey))
    story.append(Spacer(1, 10))

    events_data = [['#', 'Class', 'Confidence', 'Severity', 'Location', 'Timestamp', 'Ack']]
    for e in events:
        events_data.append([
            str(e.id),
            e.object_class.title(),
            f"{e.confidence:.1%}",
            e.severity.upper(),
            e.location_note,
            e.timestamp.strftime('%Y-%m-%d %H:%M'),
            'Yes' if e.acknowledged else 'No',
        ])
    ev_tbl = Table(
        events_data,
        colWidths=[0.4*inch, 1.2*inch, 0.9*inch, 0.8*inch, 1.3*inch, 1.3*inch, 0.4*inch],
    )
    ev_tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0), blue),
        ('TEXTCOLOR',    (0, 0), (-1, 0), colors.white),
        ('FONTNAME',     (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, -1), 8),
        ('ALIGN',        (0, 0), (-1, -1), 'CENTER'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [light, colors.white]),
        ('GRID',         (0, 0), (-1, -1), 0.5, grey),
        ('PADDING',      (0, 0), (-1, -1), 5),
    ]))
    story.append(ev_tbl)
    story.append(PageBreak())

    # ── Captured photos ──
    events_with_images = events.exclude(full_frame='').exclude(full_frame__isnull=True)
    if events_with_images.exists():
        story.append(Paragraph("Captured Photos", h2_style))
        story.append(HRFlowable(width="100%", thickness=0.5, color=grey))
        story.append(Spacer(1, 10))

        label_style = ParagraphStyle('EvLabel', parent=styles['Normal'],
                                     fontSize=10, spaceBefore=10, spaceAfter=4)

        for e in events_with_images:
            story.append(Paragraph(
                f"<b>Event #{e.id}</b> — {e.object_class.title()} | "
                f"{e.confidence:.1%} | {e.severity.upper()} | "
                f"{e.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                label_style,
            ))
            if e.full_frame:
                try:
                    story.append(Paragraph("Full Frame:", styles['Heading3']))
                    story.append(RLImage(e.full_frame.path, width=5*inch, height=3.5*inch, kind='proportional'))
                    story.append(Spacer(1, 8))
                except Exception:
                    pass
            if e.cropped_object:
                try:
                    story.append(Paragraph("Cropped Detection:", styles['Heading3']))
                    story.append(RLImage(e.cropped_object.path, width=3*inch, height=3*inch, kind='proportional'))
                    story.append(Spacer(1, 8))
                except Exception:
                    pass
            story.append(HRFlowable(width="100%", thickness=0.3, color=colors.HexColor('#eeeeee')))

    # Footer
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=grey))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"PoolGuard Surveillance System — Report generated on {now.strftime('%Y-%m-%d %H:%M:%S')}",
        footer_style,
    ))

    doc.build(story)
    buffer.seek(0)
    filename = f"poolguard_report_{now.strftime('%Y%m%d_%H%M%S')}.pdf"
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Live video feed ───────────────────────────────────────────────────────────

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
                    label      = model.names[class_id]
                    label_lc   = label.lower()

                    last = _feed_last_sent.get(label, 0)
                    if now - last < _FEED_COOLDOWN:
                        continue
                    _feed_last_sent[label] = now

                    severity = SEVERITY_MAP.get(label, 'low')
                    message  = CLASS_MESSAGES.get(label_lc, f'{label} detected at pool.')

                    event = DetectionEvent(
                        object_class=label_lc,
                        confidence=confidence,
                        severity=severity,
                        location_note='Live Camera',
                    )

                    # ── Save full annotated frame ─────────────────────────
                    try:
                        _, full_buf = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        event.full_frame.save(
                            f'full_{timezone.now().strftime("%Y%m%d_%H%M%S%f")}.jpg',
                            ContentFile(full_buf.tobytes()),
                            save=False,
                        )
                    except Exception:
                        pass

                    # ── Save cropped detection ────────────────────────────
                    try:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        # add 10px padding, clamp to frame bounds
                        pad = 10
                        h, w = frame.shape[:2]
                        x1c = max(0, x1 - pad)
                        y1c = max(0, y1 - pad)
                        x2c = min(w, x2 + pad)
                        y2c = min(h, y2 + pad)
                        crop = frame[y1c:y2c, x1c:x2c]
                        if crop.size > 0:
                            _, crop_buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                            event.cropped_object.save(
                                f'crop_{timezone.now().strftime("%Y%m%d_%H%M%S%f")}.jpg',
                                ContentFile(crop_buf.tobytes()),
                                save=False,
                            )
                    except Exception:
                        pass

                    event.save()
                    Notification.objects.create(event=event, message=message)

            _, buffer_   = cv2.imencode('.jpg', annotated)
            frame_bytes  = buffer_.tobytes()
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
        pass
    return StreamingHttpResponse(
        generate_frames(source),
        content_type='multipart/x-mixed-replace; boundary=frame',
    )

# ── Alert recipient settings ──────────────────────────────────────────────────

@require_http_methods(["GET"])
def get_alert_emails(request):
    recipients = list(AlertRecipient.objects.filter(is_active=True).values('id', 'email', 'added_at'))
    for r in recipients:
        r['added_at'] = r['added_at'].strftime('%Y-%m-%d %H:%M:%S')
    return JsonResponse({'recipients': recipients})


@csrf_exempt
@require_http_methods(["POST"])
def save_alert_email(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    action = data.get('action')  # 'add' | 'remove'
    email  = data.get('email', '').strip().lower()

    if not email:
        return JsonResponse({'error': 'Email is required'}, status=400)

    if action == 'add':
        obj, created = AlertRecipient.objects.get_or_create(email=email)
        obj.is_active = True
        obj.save()
        return JsonResponse({'status': 'added', 'id': obj.id})

    elif action == 'remove':
        AlertRecipient.objects.filter(email=email).update(is_active=False)
        return JsonResponse({'status': 'removed'})

    return JsonResponse({'error': 'Invalid action'}, status=400)