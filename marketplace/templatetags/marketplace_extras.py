from django import template
from django.utils.translation import gettext

register = template.Library()


@register.filter
def display_unit(value):
    known_units = {
        "piece": gettext("Piece"),
        "project": gettext("Project"),
        "service": gettext("Service"),
        "carton": gettext("Carton"),
        "kilogram": gettext("Kilogram"),
        "قطعة": gettext("piece"),
        "مشروع": gettext("project"),
        "خدمة": gettext("service"),
        "كرتونة": gettext("carton"),
        "كيلوغرام": gettext("kilogram"),
    }
    return known_units.get(str(value), value)
