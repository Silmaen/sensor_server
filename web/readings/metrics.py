"""Central registry of known metrics: display names, default units.

Display names use gettext_lazy so they are translatable.
Unknown metrics fall back to their raw database name with no unit.
"""

from django.utils.translation import gettext_lazy as _

# (display_name, default_unit)
METRIC_DISPLAY = {
    "temperature": (_("Temperature"), "°C"),
    "humidity": (_("Humidity"), "%"),
    "pressure": (_("Pressure"), "hPa"),
    "uv_index": (_("UV index"), ""),
    "lux": (_("Luminosity"), "lx"),
    "rssi": (_("RSSI"), "dBm"),
    "bat_percent": (_("Battery"), "%"),
    "bat_voltage": (_("Battery voltage"), "V"),
}


def get_metric_label(metric):
    """Return the display name for a metric, or the raw name if unknown."""
    entry = METRIC_DISPLAY.get(metric)
    return str(entry[0]) if entry else metric


def get_metric_unit(metric):
    """Return the default unit for a metric, or empty string if unknown."""
    entry = METRIC_DISPLAY.get(metric)
    return entry[1] if entry else ""


def get_metrics_display_map(metrics):
    """Return a dict {metric: {"label": ..., "unit": ...}} for a list of metrics.

    Useful for passing to JavaScript contexts.
    """
    return {
        m: {"label": get_metric_label(m), "unit": get_metric_unit(m)}
        for m in metrics
    }
