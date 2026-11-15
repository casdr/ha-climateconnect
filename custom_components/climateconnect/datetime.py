"""Start and end of a ClimateConnect boiler's vacation mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.datetime import DateTimeEntity

from .entity import ClimateConnectEntity, async_add_boiler_entities
from .vacation import async_set_vacation

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ClimateConnectConfigEntry, ClimateConnectCoordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the vacation start and end of every boiler."""

    def create(
        coordinator: ClimateConnectCoordinator, serial: str
    ) -> list[ClimateConnectVacationDateTime]:
        return [
            ClimateConnectVacationDateTime(coordinator, serial, is_end=False),
            ClimateConnectVacationDateTime(coordinator, serial, is_end=True),
        ]

    async_add_boiler_entities(entry, async_add_entities, create)


class ClimateConnectVacationDateTime(ClimateConnectEntity, DateTimeEntity):
    """Start or end of the vacation period."""

    def __init__(
        self, coordinator: ClimateConnectCoordinator, serial: str, *, is_end: bool
    ) -> None:
        """Initialize the date-time."""
        key = "vacation_end" if is_end else "vacation_start"
        super().__init__(coordinator, serial, key)
        self._attr_translation_key = key
        self._is_end = is_end

    @property
    def native_value(self) -> datetime | None:
        """The start or end of the period."""
        boiler = self.boiler
        if boiler is None or boiler.vacation is None:
            return None
        return boiler.vacation.end if self._is_end else boiler.vacation.start

    async def async_set_value(self, value: datetime) -> None:
        """Change the start or end; vacation mode stays on or off."""
        if self._is_end:
            await async_set_vacation(self, end=value)
        else:
            await async_set_vacation(self, start=value)
