from django.utils.translation import gettext_lazy as _

AVAILABILITY_NONE = "none"
AVAILABILITY_IMMEDIATE = "immediate"
AVAILABILITY_2_3_BUSINESS_DAYS = "2_3_business_days"
AVAILABILITY_2_3_WEEKS = "2_3_weeks"
AVAILABILITY_UNAVAILABLE = "unavailable"

AVAILABILITY_CHOICES = [
    (AVAILABILITY_NONE, _("None")),
    (AVAILABILITY_IMMEDIATE, _("Immediate")),
    (AVAILABILITY_2_3_BUSINESS_DAYS, _("2-3 business days")),
    (AVAILABILITY_2_3_WEEKS, _("2-3 weeks")),
    (AVAILABILITY_UNAVAILABLE, _("Unavailable")),
]
