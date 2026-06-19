from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from collections import Counter
from .models import DetectionEvent, AlertRecipient


def get_recipients():
    """Return active DB recipients, falling back to settings."""
    recipients = list(AlertRecipient.objects.filter(is_active=True).values_list('email', flat=True))
    if not recipients:
        recipients = [settings.DIGEST_RECIPIENT_EMAIL]
    return recipients


def send_realtime_alert(object_class, confidence, severity, location_note):
    """Send an immediate email when a threat is detected."""
    messages = {
        "animal": "Animal intrusion detected! Escort animal away from pool area.",
        "food":   "Food remains spotted at pool perimeter. Collect before attracting pests.",
        "trash":  "Trash detected near the pool. Please remove immediately.",
        "bottle": "Plastic bottle found near pool edge. Remove to prevent water contamination.",
    }

    message = messages.get(object_class, f"{object_class} detected near the pool.")
    subject = f"PoolGuard Alert: {object_class.capitalize()} detected [{severity.upper()}]"
    body = (
        f"⚠️ PoolGuard Threat Detected\n\n"
        f"Type:       {object_class.capitalize()}\n"
        f"Severity:   {severity.upper()}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Location:   {location_note}\n"
        f"Time:       {timezone.now().strftime('%Y-%m-%d %H:%M:%S')} (Kigali)\n\n"
        f"Action required: {message}\n\n"
        f"Log in to the PoolGuard dashboard for details.\n\n"
        f"- PoolGuard System"
    )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=get_recipients(),
            fail_silently=False,
        )
        print(f"  📧 Alert email sent: {object_class} [{severity}]")
    except Exception as e:
        print(f"  ❌ Alert email failed: {e}")


def send_daily_digest():
    """Collect yesterday's detections and email a summary."""
    now = timezone.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    end = start + timedelta(days=1)

    events = DetectionEvent.objects.filter(timestamp__range=(start, end))
    total = events.count()

    if total == 0:
        subject = "PoolGuard Daily Digest - No Detections"
        body = (
            f"Daily Report for {start.strftime('%B %d, %Y')}\n\n"
            "No contamination threats were detected around the pool yesterday.\n\n"
            "- PoolGuard System"
        )
    else:
        class_counts = Counter(events.values_list('object_class', flat=True))
        breakdown = "\n".join(
            f"  - {cls}: {count} event(s)" for cls, count in class_counts.most_common()
        )
        high   = events.filter(severity='high').count()
        medium = events.filter(severity='medium').count()
        low    = events.filter(severity='low').count()

        subject = f"PoolGuard Daily Digest - {total} Detection(s) on {start.strftime('%b %d')}"
        body = (
            f"Daily Report for {start.strftime('%B %d, %Y')}\n\n"
            f"Total detections: {total}\n\n"
            f"Severity breakdown:\n"
            f"  - High:   {high}\n"
            f"  - Medium: {medium}\n"
            f"  - Low:    {low}\n\n"
            f"Detected threats:\n{breakdown}\n\n"
            "Log in to the PoolGuard dashboard for full details.\n\n"
            "- PoolGuard System"
        )

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=get_recipients(),
            fail_silently=False,
        )
        print(f"  📧 Daily digest sent for {start.strftime('%B %d, %Y')}")
    except Exception as e:
        print(f"  ❌ Daily digest failed: {e}")