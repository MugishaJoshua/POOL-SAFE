from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('signup/', views.signup_view, name='signup'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('otp-verify/', views.otp_verify_view, name='otp_verify'),

    # Dashboard
    path('', views.dashboard, name='dashboard'),

    # Detection ingestion
    path('api/ingest/', views.ingest_detection, name='ingest'),

    # Notifications
    path('api/notifications/poll/', views.poll_notifications, name='poll_notifications'),
    path('api/notifications/<int:notification_id>/read/', views.mark_read, name='mark_read'),

    # Events
    path('api/events/<int:event_id>/acknowledge/', views.acknowledge_event, name='acknowledge_event'),

    # History & stats
    path('api/history/', views.history, name='history'),
    path('api/stats/', views.stats, name='stats'),

    # Live feed
    path('video-feed/', views.video_feed, name='video_feed'),

    # PDF downloads
    path('api/report/event/<int:event_id>/pdf/', views.download_event_pdf, name='event_pdf'),
    path('api/report/full/pdf/', views.download_full_report_pdf, name='full_report_pdf'),

    # Alert recipient settings
    path('api/settings/email/', views.get_alert_emails, name='get_alert_emails'),
    path('api/settings/email/save/', views.save_alert_email, name='save_alert_email'),
]