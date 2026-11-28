"""Operating mode of a ClimateConnect boiler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .api import DeviceMode
from .const import DOMAIN
from .entity import ClimateConnectEntity, async_add_boiler_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ClimateConnectConfigEntry, ClimateConnectCoordinator

PARALLEL_UPDATES = 1

MODE_OPTIONS: dict[DeviceMode, str] = {
    DeviceMode.SMART_CONTROL: "smart_control",
    DeviceMode.SCHEDULE: "schedule",
    DeviceMode.CONTINUOUS: "continuous",
    DeviceMode.HOLIDAY: "holiday",
}
MODES_BY_OPTION = {option: mode for mode, option in MODE_OPTIONS.items()}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the mode select of every boiler."""

    def create(
        coordinator: ClimateConnectCoordinator, serial: str
    ) -> list[ClimateConnectModeSelect]:
        return [ClimateConnectModeSelect(coordinator, serial)]

    async_add_boiler_entities(entry, async_add_entities, create)


class ClimateConnectModeSelect(ClimateConnectEntity, SelectEntity):
    """The boiler's operating mode."""

    _attr_translation_key = "mode"

    def __init__(self, coordinator: ClimateConnectCoordinator, serial: str) -> None:
        """Initialize the select."""
        super().__init__(coordinator, serial, "mode")
        self._attr_options = list(MODE_OPTIONS.values())

    @property
    def current_option(self) -> str | None:
        """The current mode."""
        boiler = self.boiler
        if boiler is None:
            return None
        mode = boiler.status.device_mode or boiler.mode.mode
        return MODE_OPTIONS.get(mode) if mode else None

    async def async_select_option(self, option: str) -> None:
        """Change the mode, keeping the setpoint and schedule the boiler has."""
        mode = MODES_BY_OPTION[option]
        boiler = self.boiler
        temperature = None
        schedule = None
        if mode is DeviceMode.CONTINUOUS:
            # The app always sends the setpoint with Continuous mode. Only send one
            # the boiler reported; without it the user sets one on the Setpoint.
            temperature = boiler.mode.temperature if boiler else None
            if temperature is None:
                setpoint = er.async_get(self.hass).async_get_entity_id(
                    "number", DOMAIN, f"{self._serial}_setpoint"
                )
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="setpoint_unknown",
                    translation_placeholders={"entity_id": setpoint or "Setpoint"},
                )
        elif mode is DeviceMode.SCHEDULE and boiler:
            schedule = boiler.mode.schedule
        await self.async_write_and_hold(
            self.coordinator.client.async_set_device_mode(
                self._serial, mode, temperature=temperature, schedule=schedule
            ),
            mode=mode,
        )
