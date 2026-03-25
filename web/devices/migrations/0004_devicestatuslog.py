from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0003_device_guest_visible_metrics"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceStatusLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("time", models.DateTimeField()),
                ("alert_level", models.CharField(
                    blank=True, choices=[("", "OK"), ("warning", "Warning"), ("error", "Error")],
                    default="", max_length=16,
                )),
                ("alert_message", models.CharField(blank=True, default="", max_length=256)),
                ("device", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="status_logs",
                    to="devices.device",
                )),
            ],
            options={
                "ordering": ["-time"],
                "indexes": [
                    models.Index(fields=["device", "time"], name="devices_dev_device__6d13fb_idx"),
                ],
            },
        ),
    ]
