from django import template
from django.utils.translation import gettext as _

from readings.metrics import get_metric_label, get_metric_unit

register = template.Library()


@register.filter
def has_role(user, role):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return profile.has_role(role)


@register.filter
def get_item(dictionary, key):
    """Lookup a dict key from a template variable."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, "")
    return ""


@register.filter
def metric_label(metric):
    """Return translated display name for a metric."""
    return get_metric_label(metric)


@register.filter
def metric_unit(metric):
    """Return default unit for a metric."""
    return get_metric_unit(metric)


# Diagnostics reset-cause codes reported on the `diag` topic (see the firmware
# docs/diagnostics.md). Kept in sync with platform_diag.h ResetCause.
_RESET_CAUSE_LABELS = {
    0: "unknown",
    1: "power-on",
    2: "external",
    3: "software",
    4: "deep-sleep",
    5: "brownout",
    6: "panic",
    7: "watchdog",
}


@register.filter
def reset_cause_label(code):
    """Human-readable label for a diag reset-cause code (0-7)."""
    if code is None:
        return "-"
    return _RESET_CAUSE_LABELS.get(code, str(code))


def _diag_message_labels():
    """Human-readable labels for the diag/status `message` enum strings.

    The firmware emits fixed enum tokens (see the firmware
    docs/diagnostics.md "Health model"); the server adds its own for
    server-generated alerts. Built lazily so gettext resolves per active locale.
    """
    return {
        # Firmware diag health messages.
        "ok": _("Nominal"),
        "booted": _("Booted"),
        "fair_signal": _("Fair signal"),
        "weak_signal": _("Weak signal"),
        "low_memory": _("Low memory"),
        "missed_wakes": _("Missed wake-ups"),
        "low_battery": _("Low battery"),
        "critical_battery": _("Critical battery"),
        "reset_brownout": _("Reset: brownout"),
        "reset_panic": _("Reset: panic"),
        "reset_wdt": _("Reset: watchdog"),
        # Server-generated alert messages.
        "no_capabilities_response": _("No capabilities response"),
    }


@register.filter
def diag_message_label(message):
    """Human-readable label for a diag/status `message` enum token.

    Falls back to the raw token with underscores turned into spaces for any
    message the map does not know (e.g. a newer firmware token), so nothing is
    ever hidden.
    """
    if not message:
        return "-"
    labels = _diag_message_labels()
    if message in labels:
        return labels[message]
    return message.replace("_", " ")
