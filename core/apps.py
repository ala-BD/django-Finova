from django.apps import AppConfig


def watch_env_file(sender, **kwargs):
    """En développement, runserver redémarre aussi quand .env change (identifiants SMTP, clé LLM…)."""
    from django.conf import settings

    sender.watch_dir(settings.BASE_DIR, ".env")


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.utils.autoreload import autoreload_started

        autoreload_started.connect(watch_env_file, dispatch_uid="core_watch_env_file")
