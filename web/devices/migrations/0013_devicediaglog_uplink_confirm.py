from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0012_device_alert_diag_timestamps"),
    ]

    operations = [
        migrations.AddField(
            model_name="devicediaglog",
            name="txsent",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="devicediaglog",
            name="txok",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
