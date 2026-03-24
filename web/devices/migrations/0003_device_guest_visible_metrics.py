from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0002_device_approval_capabilities"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="guest_visible_metrics",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
