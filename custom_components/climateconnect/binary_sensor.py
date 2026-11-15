"""Binary sensors for ClimateConnect boilers: connection and fault."""

# Home Assistant builds entity descriptions with a metaclass pylint cannot follow.
# pylint: disable=unexpected-keyword-arg

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .api import CONNECTED_STATES
from .entity import ClimateConnectEntity, async_add_boiler_entities

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ClimateConnectConfigEntry, ClimateConnectCoordinator
    from .models import BoilerData

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ClimateConnectBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a boiler binary sensor."""

    is_on_fn: Callable[[BoilerData], bool | None]
    attrs_fn: Callable[[BoilerData], dict[str, Any]] | None = None


def _connected(boiler: BoilerData) -> bool | None:
    state = boiler.status.device_state
    return None if state is None else state in CONNECTED_STATES


def _fault_attrs(boiler: BoilerData) -> dict[str, Any]:
    number = boiler.status.error_number
    text = boiler.fault_text(number)
    return {
        "error_number": number,
        "message": text.message if text else None,
        "description": text.description if text else None,
    }


BINARY_SENSORS: tuple[ClimateConnectBinarySensorDescription, ...] = (
    ClimateConnectBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda boiler: boiler.status.error_number is not None,
        attrs_fn=_fault_attrs,
    ),
    ClimateConnectBinarySensorDescription(
        key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_connected,
    ),
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of every boiler."""

    def create(
        coordinator: ClimateConnectCoordinator, serial: str
    ) -> list[ClimateConnectBinarySensor]:
        return [
            ClimateConnectBinarySensor(coordinator, serial, description)
            for description in BINARY_SENSORS
        ]

    async_add_boiler_entities(entry, async_add_entities, create)


class ClimateConnectBinarySensor(ClimateConnectEntity, BinarySensorEntity):
    """A binary sensor of one boiler."""

    entity_description: ClimateConnectBinarySensorDescription

    def __init__(
        self,
        coordinator: ClimateConnectCoordinator,
        serial: str,
        description: ClimateConnectBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Whether the sensor is on."""
        boiler = self.boiler
        return self.entity_description.is_on_fn(boiler) if boiler else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra attributes, if the sensor has any."""
        boiler = self.boiler
        if boiler is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(boiler)
