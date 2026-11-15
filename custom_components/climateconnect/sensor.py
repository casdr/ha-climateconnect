"""Sensors for ClimateConnect boilers."""

# Home Assistant builds entity descriptions with a metaclass pylint cannot follow.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from dataclasses import dataclass
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

from .api import DeviceState
from .entity import ClimateConnectEntity, async_add_boiler_entities
from .statistics import CURRENCY, cost_statistic_id, energy_statistic_id

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ClimateConnectConfigEntry, ClimateConnectCoordinator
    from .models import BoilerData

PARALLEL_UPDATES = 0

DEVICE_STATES = {state: state.name.lower() for state in DeviceState}


@dataclass(frozen=True, kw_only=True)
class ClimateConnectSensorDescription(SensorEntityDescription):
    """Describes a boiler sensor."""

    value_fn: Callable[[BoilerData], float | str | datetime | None]
    attrs_fn: Callable[[BoilerData], dict[str, Any]] | None = None


def _hot_water(boiler: BoilerData) -> float | None:
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


def _energy_attrs(boiler: BoilerData) -> dict[str, Any]:
    serial = boiler.info.serial_number
    return {
        "statistic_id": energy_statistic_id(serial),
        "cost_statistic_id": cost_statistic_id(serial),
    }


def _tariff_attrs(boiler: BoilerData) -> dict[str, Any]:
    start = boiler.tariff.start if boiler.tariff else None
    return {"start_date": start.isoformat() if start else None}


SENSORS: tuple[ClimateConnectSensorDescription, ...] = (
    ClimateConnectSensorDescription(
        key="hot_water",
        translation_key="hot_water",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_hot_water,
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
        key="energy_consumption",
        translation_key="energy_consumption",
        # Not a meter: it does not follow the daily totals. The Energy dashboard
        # uses the imported statistics instead (see the attributes).
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda boiler: boiler.status.energy_consumption,
        attrs_fn=_energy_attrs,
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
    _hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of every boiler."""

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
