"""Settings de développement (SQLite)."""

from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}

# Console par défaut ; un vrai envoi SMTP est possible en local via EMAIL_BACKEND dans .env.
EMAIL_BACKEND = os.environ.get(  # noqa: F405
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
