from django.apps import AppConfig


class UserProgressConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'user_progress'
    verbose_name = 'User Progress'

    def ready(self):
        from . import signals  # noqa: F401
