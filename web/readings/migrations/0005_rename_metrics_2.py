from django.db import migrations

# Additional aliases missed in 0004
ALIASES = {
    "humi": "humidity",
    "battery_v": "bat_voltage",
    "battery_pct": "bat_percent",
    "light_lux": "lux",
}

TABLES = [
    "readings_sensorreading",
    "readings_hourly",
    "readings_daily",
]


def build_sql(aliases, tables):
    statements = []

    statements.append(
        "SELECT decompress_chunk(c, if_compressed => true) "
        "FROM show_chunks('readings_sensorreading') c;"
    )

    for table in tables:
        for old, new in aliases.items():
            statements.append(
                f"UPDATE {table} SET metric = '{new}' WHERE metric = '{old}';"
            )

    statements.append(
        "SELECT compress_chunk(c, if_not_compressed => true) "
        "FROM show_chunks('readings_sensorreading', older_than => INTERVAL '7 days') c;"
    )

    return "\n".join(statements)


def build_reverse_sql(aliases, tables):
    statements = []

    statements.append(
        "SELECT decompress_chunk(c, if_compressed => true) "
        "FROM show_chunks('readings_sensorreading') c;"
    )

    for table in tables:
        for old, new in aliases.items():
            statements.append(
                f"UPDATE {table} SET metric = '{old}' WHERE metric = '{new}';"
            )

    statements.append(
        "SELECT compress_chunk(c, if_not_compressed => true) "
        "FROM show_chunks('readings_sensorreading', older_than => INTERVAL '7 days') c;"
    )

    return "\n".join(statements)


class Migration(migrations.Migration):

    dependencies = [
        ("readings", "0004_rename_metrics"),
    ]

    operations = [
        migrations.RunSQL(
            sql=build_sql(ALIASES, TABLES),
            reverse_sql=build_reverse_sql(ALIASES, TABLES),
        ),
    ]
