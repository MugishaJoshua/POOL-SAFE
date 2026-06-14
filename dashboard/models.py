from django.db import models

THREAT_CLASSES = [
    ('trash', 'Trash'),
    ('food', 'Food Remains'),
    ('animal', 'Animal'),
    ('bottle', 'Plastic Bottle'),
    ('littering', 'Littering Behaviors'),
]

SEVERITY = [
    ('low', 'Low'),
    ('medium', 'Medium'),
    ('high', 'High'),
]


class DetectionEvent(models.Model):
    object_class = models.CharField(max_length=20, choices=THREAT_CLASSES)
    confidence = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)
    image_path = models.CharField(max_length=500, blank=True)  # kept for backward compat
    severity = models.CharField(max_length=10, choices=SEVERITY, default='medium')
    location_note = models.CharField(max_length=200, blank=True, default='Pool Perimeter')
    acknowledged = models.BooleanField(default=False)
    synced_to_cloud = models.BooleanField(default=False)

    # ── New image fields ──────────────────────────────────────────────────────
    full_frame = models.ImageField(
        upload_to='detections/full_frames/',
        null=True, blank=True,
        help_text='Full camera frame at the moment of detection'
    )
    cropped_object = models.ImageField(
        upload_to='detections/cropped/',
        null=True, blank=True,
        help_text='Tight crop around the detected object'
    )
    # ─────────────────────────────────────────────────────────────────────────

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.object_class} @ {self.timestamp:%Y-%m-%d %H:%M:%S} ({self.confidence:.7%})"


class Notification(models.Model):
    event = models.ForeignKey(DetectionEvent, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification [{self.event.object_class}] - {'read' if self.read else 'unread'}"


class AlertRecipient(models.Model):
    """Stores email addresses that receive real-time alert notifications."""
    email = models.EmailField(unique=True)
    added_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.email