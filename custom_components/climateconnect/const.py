"""Constants for the Itho Daalderop ClimateConnect integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "climateconnect"
MANUFACTURER: Final = "Itho Daalderop"
DEFAULT_MODEL: Final = "ClimateConnect boiler"

CONF_UPDATE_INTERVAL: Final = "update_interval"
DEFAULT_UPDATE_INTERVAL_SECONDS: Final = 300
MIN_UPDATE_INTERVAL_SECONDS: Final = 30
MAX_UPDATE_INTERVAL_SECONDS: Final = 3600

# Boiler list, vacation mode, faults and tariff change rarely.
SLOW_INTERVAL: Final = timedelta(minutes=15)
# Energy history is per day; importing it hourly keeps today's row current.
ENERGY_INTERVAL: Final = timedelta(hours=1)
# A boiler takes seconds to report a change. Changed values are shown for up to
# HOLD_TIME, and the boiler is read again CONFIRM_DELAY seconds after a change.
HOLD_TIME: Final = timedelta(seconds=90)
CONFIRM_DELAY: Final = 30
# An unsupported sign-in page this many times in a row stops polling.
UNEXPECTED_PAGE_LIMIT: Final = 3
# Exponential back-off on transient errors, capped at this interval.
MAX_BACKOFF: Final = timedelta(minutes=30)

# Setpoint range offered for Continuous mode and schedules. The app's own limits
# are not known; it was seen sending 65 and 75 degrees.
MIN_TEMPERATURE: Final = 40
MAX_TEMPERATURE: Final = 80
# Sent with Continuous mode when the boiler has not reported a setpoint.
DEFAULT_TEMPERATURE: Final = 65

WEEKDAYS: Final = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

ISSUE_UNEXPECTED_PAGE: Final = "unexpected_sign_in_page"

SERVICE_SET_SCHEDULE: Final = "set_schedule"
SERVICE_GET_SCHEDULE: Final = "get_schedule"
SERVICE_SET_TARIFF: Final = "set_tariff"
