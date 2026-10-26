"""Settings de développement (SQLite)."""

import os

from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# Console par défaut (rien n'est envoyé) ; EMAIL_BACKEND dans .env permet l'envoi réel en SMTP.
# Auparavant forcé à la console : les invitations n'étaient jamais envoyées, même configurées.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
