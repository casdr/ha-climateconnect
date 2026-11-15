"""Diagnostics for ClimateConnect, with credentials and serial numbers redacted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

if TYPE_CHECKING:
    from datetime import date, datetime

    from homeassistant.core import HomeAssistant

    from .coordinator import ClimateConnectConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "title", "unique_id", "serialNumber"}


def _iso(moment: date | datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Boilers are numbered in order instead of showing their serial numbers.
    """
    coordinator = entry.runtime_data
    client = coordinator.client
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "signed_in": client.account_id is not None,
        "token_expires": _iso(client.token_expires),
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "boilers": [
            {
                "boiler": f"boiler_{index}",
                "model": boiler.info.model,
                "item_code": boiler.info.item_code,
                "installation_date": _iso(boiler.info.installation_date),
                "status": async_redact_data(boiler.status.raw, TO_REDACT),
                "mode": boiler.mode.mode,
                "temperature": boiler.mode.temperature,
                "schedule": boiler.mode.schedule,
                "vacation": {
                    "active": boiler.vacation.active,
                    "start": _iso(boiler.vacation.start),
                    "end": _iso(boiler.vacation.end),
                }
                if boiler.vacation
                else None,
                "faults": [
                    {
                        "error_number": fault.error_number,
                        "started": _iso(fault.started),
                        "duration": fault.duration,
                    }
                    for fault in boiler.faults
                ],
                "fault_texts": len(boiler.fault_texts),
                "tariff": {
                    "price": boiler.tariff.price,
                    "start": _iso(boiler.tariff.start),
                }
                if boiler.tariff
                else None,
            }
            for index, boiler in enumerate(coordinator.data.boilers.values(), 1)
        ],
    }
