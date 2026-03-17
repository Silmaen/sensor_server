from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("readings", "0002_aggregates_retention"),
    ]

    operations = [
        migrations.RunSQL(
            sql="SELECT remove_retention_policy('readings_sensorreading', if_exists => TRUE);",
            reverse_sql="""
                SELECT add_retention_policy('readings_sensorreading',
                    drop_after    => INTERVAL '90 days',
                    if_not_exists => TRUE
                );
            """,
        ),
    ]
