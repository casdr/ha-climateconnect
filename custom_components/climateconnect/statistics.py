"""Import the boiler's energy use and costs per day as external statistics.

The API returns the whole history in one call, so every import rebuilds the running
sums from the first day. Importing the same days again overwrites the rows with the
same values, and today's row grows during the day. The API's days start at 00:00
UTC; each day becomes one hourly row at that moment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .api import EnergyDay

_LOGGER = logging.getLogger(__name__)

CURRENCY = "EUR"


def energy_statistic_id(serial: str) -> str:
    """External statistic id of a boiler's energy use."""
    return f"{DOMAIN}:{serial.lower()}_energy"


def cost_statistic_id(serial: str) -> str:
    """External statistic id of a boiler's energy costs."""
    return f"{DOMAIN}:{serial.lower()}_costs"


def _rows(values: list[tuple[EnergyDay, float]]) -> list[StatisticData]:
    rows: list[StatisticData] = []
    running = 0.0
    for day, value in values:
        running += value
        rows.append(StatisticData(start=day.start, state=value, sum=running))
    return rows


def async_import_energy(
    hass: HomeAssistant, serial: str, name: str, days: list[EnergyDay]
) -> None:
    """Import energy use and, where the API has them, costs."""
    if not days:
        return
    _LOGGER.debug("Importing %d days of energy use", len(days))
    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{name} energy",
            source=DOMAIN,
            statistic_id=energy_statistic_id(serial),
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        _rows([(day, day.consumption) for day in days]),
    )
    costs = [(day, day.costs) for day in days if day.costs is not None]
    if costs:
        async_add_external_statistics(
            hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=f"{name} costs",
                source=DOMAIN,
                statistic_id=cost_statistic_id(serial),
                unit_class=None,
                unit_of_measurement=CURRENCY,
            ),
            _rows(costs),
        )
