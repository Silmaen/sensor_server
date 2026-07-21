from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0013_devicediaglog_uplink_confirm"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="diag_capable",
            field=models.BooleanField(default=False),
        ),
    ]
