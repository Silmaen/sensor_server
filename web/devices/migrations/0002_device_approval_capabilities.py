from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="device",
            name="is_online",
        ),
        migrations.AddField(
            model_name="device",
            name="is_approved",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="device",
            name="hardware_id",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AddField(
            model_name="device",
            name="capabilities",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="device",
            name="publish_interval",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="device",
            name="alert_level",
            field=models.CharField(
                blank=True, choices=[("", "OK"), ("warning", "Warning"), ("error", "Error")],
                default="", max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="device",
            name="alert_message",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.AddField(
            model_name="device",
            name="capabilities_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
