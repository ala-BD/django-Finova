from django.db import models


class Category(models.Model):
    """M4 : Catégorie de dépense (Linda)."""

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="categories",
        verbose_name="groupe",
    )
    name = models.CharField("nom", max_length=64)
    color = models.CharField("couleur", max_length=7, default="#0f5f59")
    icon = models.CharField("icône", max_length=32, blank=True)

    class Meta:
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"
        ordering = ["name"]

    def __str__(self):
        return self.name
