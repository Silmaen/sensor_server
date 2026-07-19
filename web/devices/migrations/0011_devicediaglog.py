import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0010_calibration_mirror"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceDiagLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("time", models.DateTimeField()),
                ("level", models.CharField(blank=True, choices=[("ok", "OK"), ("info", "Info"), ("warning", "Warning"), ("error", "Error")], default="ok", max_length=16)),
                ("message", models.CharField(blank=True, default="", max_length=256)),
                ("reset_cause", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("boot", models.PositiveIntegerField(blank=True, null=True)),
                ("miss", models.PositiveIntegerField(blank=True, null=True)),
                ("wake_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("seq", models.PositiveIntegerField(blank=True, null=True)),
                ("pubfail", models.PositiveIntegerField(blank=True, null=True)),
                ("rssi", models.IntegerField(blank=True, null=True)),
                ("heap", models.PositiveIntegerField(blank=True, null=True)),
                ("battery_percent", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="diag_logs", to="devices.device")),
            ],
            options={
                "ordering": ["-time"],
            },
        ),
        migrations.AddIndex(
            model_name="devicediaglog",
            index=models.Index(fields=["device", "time"], name="devices_dev_device__78c06d_idx"),
        ),
    ]
