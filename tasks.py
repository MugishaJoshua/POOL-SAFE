from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from collections import Counter



def send_daily_digest():
    """Collect yesterday's detections and email a summary to the pool manager"""
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
        # Break down by class
        class_counts = Counter(events.values_list('detected_class', flat=True))
        breakdown = "\n".join(
            f"  • {cls}: {count} event(s)" for cls, count in class_counts.most_common()
        )

        # Severity counts
        high = events.filter(severity='high').count()
        medium = events.filter(severity='medium').count()
        low = events.filter(severity='low').count()

        subject = f"PoolGuard Daily Digest - {total} Detection(s) on {start.strftime('%b %d')}"
        body = (
            f"Daily Report for {start.strftime('%B %d, %Y')}\n\n"
            f"Total detections: {total}\n\n"
            f"Severity breakdown:\n"
            f"  • High:   {high}\n"
            f"  • Medium: {medium}\n"
            f"  • Low:    {low}\n\n"
            f"Detected threats:\n{breakdown}\n\n"
            "Log in to the PoolGuard dashboard for full details.\n\n"
            "- PoolGuard System"
        )

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[settings.DIGEST_RECIPIENT_EMAIL],
        fail_silently=False,
    )