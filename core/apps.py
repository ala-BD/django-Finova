from django.apps import AppConfig
from django.conf import settings


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        if settings.DEBUG:
            try:
                from django.utils.autoreload import autoreload_started

                def _watch_env(sender, **kwargs):
                    sender.watch_dir(settings.BASE_DIR, ".env*")

                autoreload_started.connect(_watch_env, weak=False)
            except Exception:
                pass
