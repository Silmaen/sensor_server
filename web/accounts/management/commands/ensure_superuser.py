from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from accounts.models import UserProfile


class Command(BaseCommand):
    help = "Create the superuser from env vars if it does not exist"

    def handle(self, *args, **options):
        username = settings.DJANGO_SUPERUSER_USERNAME
        email = settings.DJANGO_SUPERUSER_EMAIL
        password = settings.DJANGO_SUPERUSER_PASSWORD

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' already exists.")
            return

        user = User.objects.create_superuser(
            username=username, email=email, password=password
        )
        UserProfile.objects.create(user=user, role="admin")
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' created."))
