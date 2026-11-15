"""An in-memory stand-in for ClimateConnectClient."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from custom_components.climateconnect.api import (
    BoilerInfo,
    DeviceMode,
    DeviceModeSettings,
    DeviceStatus,
    EnergyDay,
    Fault,
    FaultText,
    Schedule,
    Tariff,
    VacationMode,
    parse_schedule,
)

from .helpers import EMAIL, SERIAL, load_json


class FakeClient:
    """Returns fixture data and records the changes it is asked to make."""

    def __init__(self) -> None:
        """Start with one boiler, as in the fixtures."""
        self.account_id: str | None = None
        self.token_expires = datetime(2027, 11, 15, 15, 45, tzinfo=UTC)
        self.fail_with: Exception | None = None
        self.fail_energy_with: Exception | None = None
        self.fail_status_for: dict[str, Exception] = {}
        self.fail_fault_texts_with: Exception | None = None
        self.sign_ins = 0
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.boilers = [
            BoilerInfo(
                serial_number=SERIAL,
                model="Smart-upp boiler 80L Mono",
                item_code="03-00000",
                installation_date=date(2025, 2, 20),
            )
        ]
        self.status = DeviceStatus.from_api(load_json("device_status.json")["result"])
        self.mode = DeviceModeSettings.from_api(load_json("device_mode.json")["result"])
        self.vacation = VacationMode(
            active=False,
            start=datetime(2026, 12, 23, 17, tzinfo=UTC),
            end=datetime(2026, 12, 28, 2, tzinfo=UTC),
        )
        self.faults = [
            Fault(
                error_number=7,
                started=datetime(2026, 10, 27, 7, 33, tzinfo=UTC),
                duration=3600,
            )
        ]
        self.fault_texts = {
            7: FaultText(message="Elementfout", description="Element wordt niet warm.")
        }
        self.tariff = Tariff(price=0.23, start=date(2025, 6, 17))
        self.energy = [
            EnergyDay(
                start=datetime(2026, 11, 13, tzinfo=UTC), consumption=8.6, costs=1.98
            ),
            EnergyDay(
                start=datetime(2026, 11, 14, tzinfo=UTC), consumption=9.8, costs=2.25
            ),
        ]

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self._check()
        self.calls.append((name, args, kwargs))

    async def async_sign_in(self) -> str:
        """Sign in."""
        self._check()
        self.sign_ins += 1
        self.account_id = EMAIL
        return EMAIL

    async def async_get_boilers(self) -> list[BoilerInfo]:
        """Return the boilers."""
        self._check()
        return self.boilers

    async def async_get_device_status(self, serial: str) -> DeviceStatus:
        """Return the status."""
        self._check()
        if serial in self.fail_status_for:
            raise self.fail_status_for[serial]
        return self.status

    async def async_get_device_mode(self, _serial: str) -> DeviceModeSettings:
        """Return the mode."""
        self._check()
        return self.mode

    async def async_get_vacation_mode(self, _serial: str) -> VacationMode:
        """Return vacation mode."""
        self._check()
        return self.vacation

    async def async_get_fault_history(self, _serial: str) -> list[Fault]:
        """Return the faults."""
        self._check()
        return self.faults

    async def async_get_fault_texts(self, _serial: str) -> dict[int, FaultText]:
        """Return the fault texts."""
        self._check()
        if self.fail_fault_texts_with is not None:
            raise self.fail_fault_texts_with
        return self.fault_texts

    async def async_get_tariff(self, _serial: str, _today: date) -> Tariff:
        """Return the tariff."""
        self._check()
        return self.tariff

    async def async_get_energy_days(
        self, _serial: str, _now: datetime
    ) -> list[EnergyDay]:
        """Return the energy history."""
        if self.fail_energy_with is not None:
            raise self.fail_energy_with
        return self.energy

    async def async_set_device_mode(
        self,
        serial: str,
        mode: DeviceMode,
        *,
        temperature: float | None = None,
        schedule: Schedule | None = None,
    ) -> None:
        """Record a mode change and apply it."""
        self._record(
            "set_device_mode", serial, mode, temperature=temperature, schedule=schedule
        )
        self.mode = DeviceModeSettings(
            mode=mode,
            temperature=temperature
            if temperature is not None
            else self.mode.temperature,
            schedule=schedule if schedule is not None else parse_schedule({}),
        )

    async def async_set_boost(self, serial: str, *, active: bool) -> None:
        """Record a boost change."""
        self._record("set_boost", serial, active=active)

    async def async_set_vacation_mode(
        self, serial: str, *, active: bool, start: datetime, end: datetime
    ) -> None:
        """Record a vacation change and apply it."""
        self._record("set_vacation_mode", serial, active=active, start=start, end=end)
        self.vacation = VacationMode(active=active, start=start, end=end)

    async def async_set_tariff(self, price: float, start: date) -> None:
        """Record a tariff change."""
        self._record("set_tariff", price, start)
