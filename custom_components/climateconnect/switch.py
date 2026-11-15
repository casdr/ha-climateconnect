"""Switches for ClimateConnect boilers: boost and vacation mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity

from .entity import ClimateConnectEntity, async_add_boiler_entities
from .vacation import async_set_vacation

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
    """Set up the switches of every boiler."""

    def create(
        coordinator: ClimateConnectCoordinator, serial: str
    ) -> list[SwitchEntity]:
        return [
            ClimateConnectBoostSwitch(coordinator, serial),
            ClimateConnectVacationSwitch(coordinator, serial),
        ]

    async_add_boiler_entities(entry, async_add_entities, create)


class ClimateConnectBoostSwitch(ClimateConnectEntity, SwitchEntity):
    """Boost: heat the boiler now."""

    _attr_translation_key = "boost"

    def __init__(self, coordinator: ClimateConnectCoordinator, serial: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, serial, "boost")

    @property
    def is_on(self) -> bool | None:
        """Whether boost is active."""
        boiler = self.boiler
        return boiler.status.boost_active if boiler else None

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Start boosting."""
        await self.async_write_and_hold(
            self.coordinator.client.async_set_boost(self._serial, active=True),
            boost=True,
        )

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Stop boosting."""
        await self.async_write_and_hold(
            self.coordinator.client.async_set_boost(self._serial, active=False),
            boost=False,
        )


class ClimateConnectVacationSwitch(ClimateConnectEntity, SwitchEntity):
    """Vacation mode, for the period set by the vacation start and end."""

    _attr_translation_key = "vacation_mode"

    def __init__(self, coordinator: ClimateConnectCoordinator, serial: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, serial, "vacation_mode")

    @property
    def is_on(self) -> bool | None:
        """Whether vacation mode is on."""
        boiler = self.boiler
        return boiler.vacation.active if boiler and boiler.vacation else None

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Turn vacation mode on."""
        await async_set_vacation(self, active=True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn vacation mode off."""
        await async_set_vacation(self, active=False)
