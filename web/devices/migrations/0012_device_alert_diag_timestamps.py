from django.db import migrations, models


def backfill_alert_updated_at(apps, schema_editor):
    """Start the re-assertion clock for alerts latched before this field existed.

    An already-flagged device gets ``alert_updated_at`` seeded from its
    ``last_seen`` so the ingestion service's staleness clean can reason about it
    (rather than treating it as never asserted).
    """
    Device = apps.get_model("devices", "Device")
    Device.objects.exclude(alert_level="").filter(
        alert_updated_at__isnull=True
    ).update(alert_updated_at=models.F("last_seen"))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0011_devicediaglog"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="alert_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="device",
            name="diag_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_alert_updated_at, noop),
    ]
