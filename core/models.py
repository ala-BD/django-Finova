from django.conf import settings
from django.db import models


class AIRequestLog(models.Model):
    """Journal des appels LLM (latence, succès) pour les métriques de soutenance."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_requests",
    )
    module = models.CharField(max_length=32)
    feature = models.CharField(max_length=64)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    success = models.BooleanField(default=False)
    error_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "journal IA"
        verbose_name_plural = "journaux IA"

    def __str__(self):
        status = "ok" if self.success else "erreur"
        return f"{self.module}.{self.feature} ({status})"
