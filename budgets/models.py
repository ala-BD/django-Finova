from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower

# Modèles M4 (Linda) : Category, Budget.
# Category est livré ici en version minimale (diagramme de classes §4.2) car M2 en dépend
# pour démarrer : Linda le complète et ajoute Budget dans une migration 0002.


class Category(models.Model):
    """Catégorie de dépenses d'un groupe (Restaurant, Transport…)."""

    group = models.ForeignKey(
        "groups.Group",
        on_delete=models.CASCADE,
        related_name="categories",
        verbose_name="groupe",
    )
    name = models.CharField("nom", max_length=60)
    color = models.CharField(
        "couleur",
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Couleur au format #RRGGBB.")],
    )
    icon = models.CharField("icône", max_length=40, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"
        constraints = [
            # Nom unique par groupe, sans tenir compte de la casse (« Resto » = « resto »)
            models.UniqueConstraint(Lower("name"), "group", name="unique_category_name_per_group"),
        ]

    def __str__(self):
        return self.name
