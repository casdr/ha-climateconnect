"""Setpoint of a ClimateConnect boiler in Continuous mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfTemperature

from .api import DeviceMode
from .const import MAX_TEMPERATURE, MIN_TEMPERATURE
from .entity import ClimateConnectEntity, async_add_boiler_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ClimateConnectConfigEntry, ClimateConnectCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the setpoint of every boiler."""

    def create(
        coordinator: ClimateConnectCoordinator, serial: str
    ) -> list[ClimateConnectSetpoint]:
        return [ClimateConnectSetpoint(coordinator, serial)]

    async_add_boiler_entities(entry, async_add_entities, create)


class ClimateConnectSetpoint(ClimateConnectEntity, NumberEntity):
    """Water temperature in Continuous mode. Setting it selects Continuous mode."""

    _attr_translation_key = "setpoint"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = MIN_TEMPERATURE
    _attr_native_max_value = MAX_TEMPERATURE
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ClimateConnectCoordinator, serial: str) -> None:
        """Initialize the number."""
        super().__init__(coordinator, serial, "setpoint")

    @property
    def native_value(self) -> float | None:
        """The setpoint."""
        boiler = self.boiler
        return boiler.mode.temperature if boiler else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the setpoint; the boiler switches to Continuous mode."""
        await self.async_write_and_hold(
            self.coordinator.client.async_set_device_mode(
                self._serial, DeviceMode.CONTINUOUS, temperature=value
            ),
            mode=DeviceMode.CONTINUOUS,
            temperature=value,
        )
