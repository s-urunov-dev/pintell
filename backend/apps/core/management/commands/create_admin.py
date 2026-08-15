"""Create the admin superuser from environment variables, if configured.

Idempotent: an existing username is left untouched. Nothing happens unless
all three DJANGO_SUPERUSER_* variables are present.
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the Django superuser defined by DJANGO_SUPERUSER_* env vars."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

        if not (username and password):
            self.stdout.write("DJANGO_SUPERUSER_* not set — skipping admin creation.")
            return

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' already exists — nothing to do.")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' created."))
