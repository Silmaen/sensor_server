from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE TABLE IF NOT EXISTS readings_sensorreading (
                    time        TIMESTAMPTZ NOT NULL,
                    device_id   TEXT        NOT NULL,
                    metric      TEXT        NOT NULL,
                    value       DOUBLE PRECISION NOT NULL
                );
                SELECT create_hypertable(
                    'readings_sensorreading', 'time',
                    if_not_exists => TRUE
                );
                CREATE INDEX IF NOT EXISTS idx_readings_device_metric_time
                    ON readings_sensorreading (device_id, metric, time DESC);
            """,
            reverse_sql="DROP TABLE IF EXISTS readings_sensorreading;",
        ),
    ]
