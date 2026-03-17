from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("readings", "0001_initial"),
    ]

    operations = [
        # Continuous aggregate: hourly averages
        migrations.RunSQL(
            sql="""
                CREATE MATERIALIZED VIEW IF NOT EXISTS readings_hourly
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 hour', time) AS bucket,
                    device_id,
                    metric,
                    avg(value)   AS avg_value,
                    min(value)   AS min_value,
                    max(value)   AS max_value,
                    count(*)     AS sample_count
                FROM readings_sensorreading
                GROUP BY bucket, device_id, metric
                WITH NO DATA;

                SELECT add_continuous_aggregate_policy('readings_hourly',
                    start_offset    => INTERVAL '3 hours',
                    end_offset      => INTERVAL '1 hour',
                    schedule_interval => INTERVAL '1 hour',
                    if_not_exists   => TRUE
                );
            """,
            reverse_sql="""
                SELECT remove_continuous_aggregate_policy('readings_hourly', if_exists => TRUE);
                DROP MATERIALIZED VIEW IF EXISTS readings_hourly;
            """,
        ),
        # Continuous aggregate: daily averages
        migrations.RunSQL(
            sql="""
                CREATE MATERIALIZED VIEW IF NOT EXISTS readings_daily
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 day', time) AS bucket,
                    device_id,
                    metric,
                    avg(value)   AS avg_value,
                    min(value)   AS min_value,
                    max(value)   AS max_value,
                    count(*)     AS sample_count
                FROM readings_sensorreading
                GROUP BY bucket, device_id, metric
                WITH NO DATA;

                SELECT add_continuous_aggregate_policy('readings_daily',
                    start_offset    => INTERVAL '3 days',
                    end_offset      => INTERVAL '1 day',
                    schedule_interval => INTERVAL '1 day',
                    if_not_exists   => TRUE
                );
            """,
            reverse_sql="""
                SELECT remove_continuous_aggregate_policy('readings_daily', if_exists => TRUE);
                DROP MATERIALIZED VIEW IF EXISTS readings_daily;
            """,
        ),
        # Compression policy: compress chunks older than 7 days
        migrations.RunSQL(
            sql="""
                ALTER TABLE readings_sensorreading
                    SET (timescaledb.compress,
                         timescaledb.compress_segmentby = 'device_id, metric',
                         timescaledb.compress_orderby = 'time DESC');

                SELECT add_compression_policy('readings_sensorreading',
                    compress_after => INTERVAL '7 days',
                    if_not_exists  => TRUE
                );
            """,
            reverse_sql="""
                SELECT remove_compression_policy('readings_sensorreading', if_exists => TRUE);
                ALTER TABLE readings_sensorreading SET (timescaledb.compress = false);
            """,
        ),
        # Retention policy: drop raw data older than 90 days
        # (hourly/daily aggregates are kept indefinitely)
        migrations.RunSQL(
            sql="""
                SELECT add_retention_policy('readings_sensorreading',
                    drop_after    => INTERVAL '90 days',
                    if_not_exists => TRUE
                );
            """,
            reverse_sql="""
                SELECT remove_retention_policy('readings_sensorreading', if_exists => TRUE);
            """,
        ),
    ]
