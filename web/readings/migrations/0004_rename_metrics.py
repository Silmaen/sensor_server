from django.db import migrations

# Must match METRIC_ALIASES in mqtt_bridge/services.py
ALIASES = {
    "temp": "temperature",
    "press": "pressure",
    "uv": "uv_index",
    "batv": "bat_voltage",
    "bat": "bat_percent",
    "wifi_rssi": "rssi",
}

TABLES = [
    "readings_sensorreading",
    "readings_hourly",
    "readings_daily",
]


def build_rename_sql(aliases, tables):
    """Build SQL to rename old metric names to canonical names in all tables."""
    statements = []

    # Decompress all chunks first (UPDATE not allowed on compressed chunks)
    statements.append(
        "SELECT decompress_chunk(c, if_compressed => true) "
        "FROM show_chunks('readings_sensorreading') c;"
    )

    for table in tables:
        for old, new in aliases.items():
            statements.append(
                f"UPDATE {table} SET metric = '{new}' WHERE metric = '{old}';"
            )

    # Recompress
    statements.append(
        "SELECT compress_chunk(c, if_not_compressed => true) "
        "FROM show_chunks('readings_sensorreading') c "
        "WHERE c < now() - INTERVAL '7 days';"
    )

    return "\n".join(statements)


def build_reverse_sql(aliases, tables):
    """Build SQL to revert canonical names back to old names."""
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
        "FROM show_chunks('readings_sensorreading') c "
        "WHERE c < now() - INTERVAL '7 days';"
    )

    return "\n".join(statements)


class Migration(migrations.Migration):

    dependencies = [
        ("readings", "0003_remove_retention_policy"),
    ]

    operations = [
        migrations.RunSQL(
            sql=build_rename_sql(ALIASES, TABLES),
            reverse_sql=build_reverse_sql(ALIASES, TABLES),
        ),
    ]
