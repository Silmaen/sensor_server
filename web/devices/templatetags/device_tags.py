from django import template

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
