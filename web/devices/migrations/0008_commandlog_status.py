from django.db import migrations, models


def set_status_from_acked(apps, schema_editor):
    """Backfill status for existing rows: acked → success, else pending."""
    CommandLog = apps.get_model("devices", "CommandLog")
    CommandLog.objects.filter(acked=True).update(status="success")
    CommandLog.objects.filter(acked=False).update(status="pending")


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0007_device_hw_fw_battery"),
    ]

    operations = [
        migrations.AddField(
            model_name="commandlog",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("success", "Success"),
                    ("failed", "Failed"),
                    ("timeout", "Timeout"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="commandlog",
            name="response_message",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
        migrations.RunPython(set_status_from_acked, migrations.RunPython.noop),
    ]
