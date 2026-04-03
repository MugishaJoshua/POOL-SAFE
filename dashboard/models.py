from django.db import models

THREAT_CLASSES = [
    ('trash', 'Trash'),
    ('food', 'Food Remains'),
    ('animal', 'Animal'),
    ('bottle', 'Plastic Bottle'),
    ('littering', 'Littering Behaviour'),
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
    image_path = models.CharField(max_length=500, blank=True)
    severity = models.CharField(max_length=10, choices=SEVERITY, default='medium')
    location_note = models.CharField(max_length=200, blank=True, default='Pool Perimeter')
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.object_class} @ {self.timestamp:%Y-%m-%d %H:%M:%S} ({self.confidence:.0%})"


class Notification(models.Model):
    event = models.ForeignKey(DetectionEvent, on_delete=models.CASCADE, related_name='notifications')
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification [{self.event.object_class}] - {'read' if self.read else 'unread'}"
