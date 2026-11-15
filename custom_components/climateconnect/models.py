"""Data the coordinator keeps per boiler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import (
        BoilerInfo,
        DeviceModeSettings,
        DeviceStatus,
        EnergyDay,
        Fault,
        FaultText,
        Tariff,
        VacationMode,
    )


@dataclass(slots=True)
class BoilerData:
    """Everything known about one boiler."""

    info: BoilerInfo
    status: DeviceStatus
    mode: DeviceModeSettings
    vacation: VacationMode | None
    faults: list[Fault]
    tariff: Tariff | None
    fault_texts: dict[int, FaultText]
    energy_days: list[EnergyDay] = field(default_factory=list)

    def fault_text(self, error_number: int | None) -> FaultText | None:
        """Return the app's texts for a fault code, if known."""
        return None if error_number is None else self.fault_texts.get(error_number)


@dataclass(slots=True)
class ClimateConnectData:
    """All boilers of one account, by serial number."""

    boilers: dict[str, BoilerData] = field(default_factory=dict)
