"""Sensors for ClimateConnect boilers."""

# Home Assistant builds entity descriptions with a metaclass pylint cannot follow.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .api import DeviceState
from .const import DOMAIN
from .entity import ClimateConnectEntity, async_add_boiler_entities
from .statistics import CURRENCY, cost_statistic_id, energy_statistic_id

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .api import EnergyDay
    from .coordinator import ClimateConnectConfigEntry, ClimateConnectCoordinator
    from .models import BoilerData

PARALLEL_UPDATES = 0

REMOVED_ENERGY_KEY = "energy_consumption"

DEVICE_STATES = {state: state.name.lower() for state in DeviceState}


@dataclass(frozen=True, kw_only=True)
class ClimateConnectSensorDescription(SensorEntityDescription):
    """Describes a boiler sensor."""

    value_fn: Callable[[BoilerData], float | str | datetime | None]
    attrs_fn: Callable[[BoilerData], dict[str, Any]] | None = None


def _energy_level(boiler: BoilerData) -> float | None:
    content = boiler.status.boiler_content
    return None if content is None else round(content * 100, 1)


def _device_state(boiler: BoilerData) -> str | None:
    state = boiler.status.device_state
    return DEVICE_STATES[state] if state else None


def _last_fault(boiler: BoilerData) -> datetime | None:
    return boiler.faults[0].started if boiler.faults else None


def _fault_attrs(boiler: BoilerData) -> dict[str, Any]:
    history = []
    for fault in boiler.faults:
        text = boiler.fault_text(fault.error_number)
        history.append(
            {
                "error_number": fault.error_number,
                "message": text.message if text else None,
                "started": fault.started.isoformat() if fault.started else None,
                "duration": fault.duration,
            }
        )
    latest = history[0] if history else {}
    return {
        "error_number": latest.get("error_number"),
        "message": latest.get("message"),
        "duration": latest.get("duration"),
        "history": history,
    }


def _energy_day(boiler: BoilerData, days_ago: int) -> EnergyDay | None:
    """Return the API's row for today or an earlier day (days start at 00:00 UTC)."""
    wanted = dt_util.utcnow().date() - timedelta(days=days_ago)
    return next(
        (day for day in reversed(boiler.energy_days) if day.start.date() == wanted),
        None,
    )


def _energy_value(days_ago: int) -> Callable[[BoilerData], float | None]:
    def value(boiler: BoilerData) -> float | None:
        day = _energy_day(boiler, days_ago)
        return day.consumption if day else None

    return value


def _energy_attrs(days_ago: int) -> Callable[[BoilerData], dict[str, Any]]:
    def attrs(boiler: BoilerData) -> dict[str, Any]:
        day = _energy_day(boiler, days_ago)
        serial = boiler.info.serial_number
        return {
            "costs": day.costs if day else None,
            "period_start": day.start.isoformat() if day else None,
            "statistic_id": energy_statistic_id(serial),
            "cost_statistic_id": cost_statistic_id(serial),
        }

    return attrs


def _tariff_attrs(boiler: BoilerData) -> dict[str, Any]:
    start = boiler.tariff.start if boiler.tariff else None
    return {"start_date": start.isoformat() if start else None}


SENSORS: tuple[ClimateConnectSensorDescription, ...] = (
    ClimateConnectSensorDescription(
        # The key predates the name: the app calls this the boiler's energy level.
        key="hot_water",
        translation_key="hot_water",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_energy_level,
    ),
    ClimateConnectSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        # The API reports kW (about 1 kW while heating); shown in W.
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda boiler: boiler.status.power,
    ),
    ClimateConnectSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=_energy_value(0),
        attrs_fn=_energy_attrs(0),
    ),
    ClimateConnectSensorDescription(
        key="energy_yesterday",
        translation_key="energy_yesterday",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=_energy_value(1),
        attrs_fn=_energy_attrs(1),
    ),
    ClimateConnectSensorDescription(
        key="legionella_timer",
        translation_key="legionella_timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda boiler: boiler.status.legionella_prevention_timer,
    ),
    ClimateConnectSensorDescription(
        key="last_fault",
        translation_key="last_fault",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_last_fault,
        attrs_fn=_fault_attrs,
    ),
    ClimateConnectSensorDescription(
        key="tariff",
        translation_key="tariff",
        native_unit_of_measurement=f"{CURRENCY}/{UnitOfEnergy.KILO_WATT_HOUR}",
        suggested_display_precision=2,
        value_fn=lambda boiler: boiler.tariff.price if boiler.tariff else None,
        attrs_fn=_tariff_attrs,
    ),
    ClimateConnectSensorDescription(
        key="connection_state",
        translation_key="connection_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(DEVICE_STATES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_device_state,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of every boiler."""
    # Version 0.1.1 and earlier had an energy consumption sensor. The value is the
    # app's relative usage gauge, not energy, so the sensor was removed.
    registry = er.async_get(hass)
    for serial in entry.runtime_data.data.boilers:
        if entity_id := registry.async_get_entity_id(
            "sensor", DOMAIN, f"{serial}_{REMOVED_ENERGY_KEY}"
        ):
            registry.async_remove(entity_id)

    def create(
        coordinator: ClimateConnectCoordinator, serial: str
    ) -> list[ClimateConnectSensor]:
        return [
            ClimateConnectSensor(coordinator, serial, description)
            for description in SENSORS
        ]

    async_add_boiler_entities(entry, async_add_entities, create)


class ClimateConnectSensor(ClimateConnectEntity, SensorEntity):
    """A sensor of one boiler."""

    entity_description: ClimateConnectSensorDescription

    def __init__(
        self,
        coordinator: ClimateConnectCoordinator,
        serial: str,
        description: ClimateConnectSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | datetime | None:
        """The current value."""
        boiler = self.boiler
        return self.entity_description.value_fn(boiler) if boiler else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra attributes, if the sensor has any."""
        boiler = self.boiler
        if boiler is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(boiler)
