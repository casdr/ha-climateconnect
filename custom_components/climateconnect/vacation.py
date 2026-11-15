"""Vacation mode, shared by its switch and its start and end date-times.

The app's back end keeps one period per boiler and an on/off flag. Changing either
sends both.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .api import VacationMode
from .const import DOMAIN
from .entity import async_call_api

if TYPE_CHECKING:
    from .entity import ClimateConnectEntity

DEFAULT_LENGTH = timedelta(days=7)


async def async_set_vacation(
    entity: ClimateConnectEntity,
    *,
    active: bool | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> None:
    """Change vacation mode; arguments left out keep their current value.

    A new start after the current end moves the end to one week after the start.
    """
    boiler = entity.boiler
    current = boiler.vacation if boiler else None
    now = dt_util.now()
    if active is None:
        active = current.active if current else False
    if start is not None and end is None:
        end = current.end if current else None
        if end is None or end <= start:
            end = start + DEFAULT_LENGTH
    start = start or (current.start if current else None) or now
    end = end or (current.end if current else None) or start + DEFAULT_LENGTH
    if end <= start:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="vacation_end_before_start"
        )
    if active and end <= now:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="vacation_in_past"
        )
    start, end = dt_util.as_local(start), dt_util.as_local(end)
    coordinator = entity.coordinator
    await async_call_api(
        coordinator.client.async_set_vacation_mode(
            entity.serial, active=active, start=start, end=end
        )
    )
    # The back end stores what was sent. Show it now: the refresh below may be
    # debounced, and a quick second change must start from these values.
    if boiler := entity.boiler:
        boiler.vacation = VacationMode(active=active, start=start, end=end)
        coordinator.async_update_listeners()
    coordinator.request_slow_refresh()
    await coordinator.async_request_refresh()
