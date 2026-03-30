from django import template

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
