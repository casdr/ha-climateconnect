"""Actions: weekly schedule and electricity tariff."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from .api import DeviceMode
from .const import (
    DOMAIN,
    MAX_TEMPERATURE,
    MIN_TEMPERATURE,
    SERVICE_GET_SCHEDULE,
    SERVICE_SET_SCHEDULE,
    SERVICE_SET_TARIFF,
    WEEKDAYS,
)
from .entity import async_call_api

if TYPE_CHECKING:
    from datetime import date, time

    from .api import Schedule
    from .coordinator import ClimateConnectCoordinator

ATTR_DEVICE_ID = "device_id"
ATTR_SCHEDULE = "schedule"
ATTR_TIME = "time"
ATTR_TEMPERATURE = "temperature"
ATTR_PRICE = "price"
ATTR_START_DATE = "start_date"

SWITCH_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_TIME): cv.time,
        probatio.Required(ATTR_TEMPERATURE): probatio.All(
            probatio.Coerce(float),
            probatio.Range(min=MIN_TEMPERATURE, max=MAX_TEMPERATURE),
        ),
    }
)
SET_SCHEDULE_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): cv.string,
        probatio.Required(ATTR_SCHEDULE): probatio.Schema(
            {probatio.Optional(day): [SWITCH_SCHEMA] for day in WEEKDAYS}
        ),
    }
)
GET_SCHEDULE_SCHEMA = probatio.Schema({probatio.Required(ATTR_DEVICE_ID): cv.string})
SET_TARIFF_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): cv.string,
        probatio.Required(ATTR_PRICE): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0, max=10)
        ),
        probatio.Optional(ATTR_START_DATE): cv.date,
    }
)


def _boiler_for(
    hass: HomeAssistant, device_id: str
) -> tuple[ClimateConnectCoordinator, str]:
    """Return the coordinator and serial number of a boiler's device."""
    device = dr.async_get(hass).async_get(device_id)
    serial = next(
        (
            value
            for domain, value in (device.identifiers if device else ())
            if domain == DOMAIN
        ),
        None,
    )
    if device is None or serial is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_boiler"
        )
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is not None
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
        ):
            coordinator: ClimateConnectCoordinator = entry.runtime_data
            if serial in coordinator.data.boilers:
                return coordinator, serial
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="boiler_not_loaded"
    )


def _seconds(moment: time) -> int:
    return moment.hour * 3600 + moment.minute * 60 + moment.second


def schedule_from_service(value: dict[str, list[dict[str, Any]]]) -> Schedule:
    """Turn the action's weekday lists into the API's schedule."""
    schedule: Schedule = {day: {} for day in range(7)}
    for index, name in enumerate(WEEKDAYS):
        for switch in value.get(name, []):
            schedule[index][_seconds(switch[ATTR_TIME])] = switch[ATTR_TEMPERATURE]
    return schedule


def schedule_to_service(schedule: Schedule) -> dict[str, list[dict[str, Any]]]:
    """Turn the API's schedule into weekday lists, as the action takes them."""
    return {
        name: [
            {
                ATTR_TIME: f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}",
                ATTR_TEMPERATURE: temperature,
            }
            for seconds, temperature in sorted(schedule.get(index, {}).items())
        ]
        for index, name in enumerate(WEEKDAYS)
    }


async def _async_set_schedule(call: ServiceCall) -> None:
    coordinator, serial = _boiler_for(call.hass, call.data[ATTR_DEVICE_ID])
    schedule = schedule_from_service(call.data[ATTR_SCHEDULE])
    await async_call_api(
        coordinator.client.async_set_device_mode(
            serial, DeviceMode.SCHEDULE, schedule=schedule
        )
    )
    coordinator.async_hold(serial, mode=DeviceMode.SCHEDULE, schedule=schedule)


async def _async_get_schedule(call: ServiceCall) -> ServiceResponse:
    coordinator, serial = _boiler_for(call.hass, call.data[ATTR_DEVICE_ID])
    boiler = coordinator.data.boilers[serial]
    response: dict[str, Any] = {
        "mode": boiler.mode.mode.value if boiler.mode.mode else None,
        ATTR_SCHEDULE: schedule_to_service(boiler.mode.schedule),
    }
    return cast("ServiceResponse", response)


async def _async_set_tariff(call: ServiceCall) -> None:
    coordinator, _serial = _boiler_for(call.hass, call.data[ATTR_DEVICE_ID])
    start: date = call.data.get(ATTR_START_DATE) or dt_util.now().date()
    await async_call_api(
        coordinator.client.async_set_tariff(call.data[ATTR_PRICE], start)
    )
    coordinator.request_slow_refresh()
    await coordinator.async_request_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN, SERVICE_SET_SCHEDULE, _async_set_schedule, schema=SET_SCHEDULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        _async_get_schedule,
        schema=GET_SCHEDULE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_TARIFF, _async_set_tariff, schema=SET_TARIFF_SCHEMA
    )
