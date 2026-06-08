import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Create a superuser if none exists'

    def handle(self, *args, **options):
        User = get_user_model()
        if not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(
                username=os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin'),
                email=os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@poolguard.com'),
                password=os.environ.get('DJANGO_SUPERUSER_PASSWORD', 'admin123')
            )
            self.stdout.write('Superuser created.')
        else:
            self.stdout.write('Superuser already exists.')