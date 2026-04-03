from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/ingest/', views.ingest_detection, name='ingest'),
    path('api/notifications/poll/', views.poll_notifications, name='poll_notifications'),
    path('api/notifications/<int:notification_id>/read/', views.mark_read, name='mark_read'),
    path('api/events/<int:event_id>/acknowledge/', views.acknowledge_event, name='acknowledge_event'),
    path('api/history/', views.history, name='history'),
    path('api/stats/', views.stats, name='stats'),
]
