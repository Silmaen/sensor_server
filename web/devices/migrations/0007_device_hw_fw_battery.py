from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0006_add_battery_cell_count"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="hw_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="device",
            name="fw_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="device",
            name="battery_percent",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
