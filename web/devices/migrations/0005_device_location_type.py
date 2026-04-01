from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0004_devicestatuslog"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="location_type",
            field=models.CharField(
                blank=True,
                choices=[("", "—"), ("indoor", "Indoor"), ("outdoor", "Outdoor")],
                default="",
                max_length=16,
            ),
        ),
    ]
