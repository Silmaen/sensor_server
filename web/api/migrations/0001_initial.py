import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="Human-readable label for the consuming service.", max_length=128)),
                ("prefix", models.CharField(db_index=True, editable=False, help_text="Leading characters of the token, for identification.", max_length=16)),
                ("key_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("created_by", models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "API key",
                "verbose_name_plural": "API keys",
                "ordering": ["-created_at"],
            },
        ),
    ]
