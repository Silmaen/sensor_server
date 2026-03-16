from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Device",
            fields=[
                ("device_id", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("device_type", models.CharField(default="unknown", max_length=64)),
                ("display_name", models.CharField(blank=True, default="", max_length=128)),
                ("location", models.CharField(blank=True, default="", max_length=128)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("is_online", models.BooleanField(default=False)),
                ("last_seen", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["device_id"],
            },
        ),
        migrations.CreateModel(
            name="CommandLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("command", models.JSONField()),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                ("acked", models.BooleanField(default=False)),
                ("acked_at", models.DateTimeField(blank=True, null=True)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="commands", to="devices.device")),
                ("sent_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-sent_at"],
            },
        ),
    ]
