from django.core.management.base import BaseCommand
from dashboard.tasks import send_daily_digest


class Command(BaseCommand):
    help = 'Send the daily PoolGuard detection digest email'

    def handle(self, *args, **kwargs):
        self.stdout.write('Sending daily digest...')
        send_daily_digest()
        self.stdout.write(self.style.SUCCESS('Daily digest sent successfully.'))