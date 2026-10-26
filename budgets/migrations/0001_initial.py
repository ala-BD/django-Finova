import django.core.validators
import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("groups", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=60, verbose_name="nom")),
                (
                    "color",
                    models.CharField(
                        blank=True,
                        max_length=7,
                        validators=[
                            django.core.validators.RegexValidator("^#[0-9a-fA-F]{6}$", "Couleur au format #RRGGBB.")
                        ],
                        verbose_name="couleur",
                    ),
                ),
                ("icon", models.CharField(blank=True, max_length=40, verbose_name="icône")),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="categories",
                        to="groups.group",
                        verbose_name="groupe",
                    ),
                ),
            ],
            options={
                "verbose_name": "catégorie",
                "verbose_name_plural": "catégories",
                "ordering": ["name"],
                "constraints": [
                    models.UniqueConstraint(
                        django.db.models.functions.text.Lower("name"),
                        models.F("group"),
                        name="unique_category_name_per_group",
                    )
                ],
            },
        ),
    ]
