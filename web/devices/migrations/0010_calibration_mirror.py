from django.db import migrations


def forward(apps, schema_editor):
    """Copy legacy offsets (config['calibration'] = {temp,humi,press}) into the
    unified calibration mirror (Device.calibration) as cal_<metric>.

    Non-destructive: existing mirror keys win, and config is left untouched.
    """
    Device = apps.get_model("devices", "Device")
    for device in Device.objects.all():
        legacy = (device.config or {}).get("calibration") or {}
        if not legacy:
            continue
        mirror = device.calibration or {}
        changed = False
        for metric in ("temp", "humi", "press"):
            value = legacy.get(metric)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                key = "cal_" + metric
                if key not in mirror:
                    mirror[key] = round(float(value), 2)
                    changed = True
        if changed:
            device.calibration = mirror
            device.save(update_fields=["calibration"])


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0009_remove_device_hw_code_device_calibration_and_more"),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
