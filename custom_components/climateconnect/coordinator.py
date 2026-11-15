"""Data update coordinator for ClimateConnect."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ClimateConnectAuthError,
    ClimateConnectClient,
    ClimateConnectError,
    ClimateConnectNotFoundError,
    ClimateConnectRequestError,
    ClimateConnectUnexpectedPageError,
)
from .const import (
    CONF_UPDATE_INTERVAL,
    CONFIRM_DELAY,
    DEFAULT_MODEL,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DOMAIN,
    ENERGY_INTERVAL,
    HOLD_TIME,
    ISSUE_UNEXPECTED_PAGE,
    MAX_BACKOFF,
    SLOW_INTERVAL,
    UNEXPECTED_PAGE_LIMIT,
)
from .models import BoilerData, ClimateConnectData
from .statistics import async_import_energy

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .api import (
        BoilerInfo,
        DeviceMode,
        EnergyDay,
        Fault,
        FaultText,
        Schedule,
        Tariff,
        VacationMode,
    )

_LOGGER = logging.getLogger(__name__)

type ClimateConnectConfigEntry = ConfigEntry[ClimateConnectCoordinator]


@dataclasses.dataclass(slots=True)
class _Hold:
    """Values a boiler was told to take, shown until it reports them or HOLD_TIME.

    The boiler takes seconds to apply a change, so a read right after a write still
    returns the old value.
    """

    until: datetime
    mode: DeviceMode | None = None
    temperature: float | None = None
    schedule: Schedule | None = None
    boost: bool | None = None

    def settle(self, boiler: BoilerData) -> None:
        """Forget the values the boiler now reports itself."""
        if self.mode is not None and boiler.status.device_mode is self.mode:
            self.mode = None
        if self.temperature is not None and boiler.mode.temperature == self.temperature:
            self.temperature = None
        if self.schedule is not None and boiler.mode.schedule == self.schedule:
            self.schedule = None
        if self.boost is not None and boiler.status.boost_active is self.boost:
            self.boost = None

    @property
    def empty(self) -> bool:
        """Whether nothing is held any more."""
        return (self.mode, self.temperature, self.schedule, self.boost) == (
            None,
            None,
            None,
            None,
        )

    def apply(self, boiler: BoilerData) -> None:
        """Show the held values on the boiler."""
        status, mode = boiler.status, boiler.mode
        if self.mode is not None:
            status = dataclasses.replace(status, device_mode=self.mode)
            mode = dataclasses.replace(mode, mode=self.mode)
        if self.boost is not None:
            status = dataclasses.replace(status, boost_active=self.boost)
        if self.temperature is not None:
            mode = dataclasses.replace(mode, temperature=self.temperature)
        if self.schedule is not None:
            mode = dataclasses.replace(mode, schedule=self.schedule)
        boiler.status, boiler.mode = status, mode


class ClimateConnectCoordinator(DataUpdateCoordinator[ClimateConnectData]):
    """Fetches the status of every boiler of one account.

    Status and mode are fetched on every update. The boiler list, vacation mode,
    faults and tariff every SLOW_INTERVAL, and the energy history every
    ENERGY_INTERVAL.
    """

    config_entry: ClimateConnectConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ClimateConnectConfigEntry,
        client: ClimateConnectClient,
    ) -> None:
        """Initialise the coordinator."""
        seconds = entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_SECONDS
        )
        self._base_interval = timedelta(seconds=seconds)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._base_interval,
        )
        self.client = client
        self._failures = 0
        self._unexpected_pages = 0
        self._boilers: list[BoilerInfo] = []
        self._skipped: set[str] = set()
        self._fault_texts: dict[str, dict[int, FaultText]] = {}
        self._energy_days: dict[str, list[EnergyDay]] = {}
        self._holds: dict[str, _Hold] = {}
        self._unsub_confirm: CALLBACK_TYPE | None = None
        self._slow_due: datetime | None = None
        self._energy_due: datetime | None = None

    @property
    def issue_id(self) -> str:
        """Repair issue id for an unsupported sign-in page of this entry."""
        return f"{ISSUE_UNEXPECTED_PAGE}_{self.config_entry.entry_id}"

    def request_slow_refresh(self) -> None:
        """Fetch vacation mode, faults and tariff again on the next update."""
        self._slow_due = None

    @callback
    def async_hold(
        self,
        serial: str,
        *,
        mode: DeviceMode | None = None,
        temperature: float | None = None,
        schedule: Schedule | None = None,
        boost: bool | None = None,
    ) -> None:
        """Show values just sent to a boiler, and check them again in a while."""
        hold = self._holds.get(serial)
        until = dt_util.utcnow() + HOLD_TIME
        if hold is None:
            hold = self._holds[serial] = _Hold(until=until)
        hold.until = until
        if mode is not None:
            hold.mode = mode
        if temperature is not None:
            hold.temperature = temperature
        if schedule is not None:
            hold.schedule = schedule
        if boost is not None:
            hold.boost = boost
        if self.data and (boiler := self.data.boilers.get(serial)):
            hold.apply(boiler)
            self.async_update_listeners()
        if self._unsub_confirm:
            self._unsub_confirm()
        self._unsub_confirm = async_call_later(
            self.hass, CONFIRM_DELAY, self._async_confirm
        )

    async def _async_confirm(self, _now: datetime) -> None:
        self._unsub_confirm = None
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Cancel the pending confirmation refresh."""
        if self._unsub_confirm:
            self._unsub_confirm()
            self._unsub_confirm = None
        await super().async_shutdown()

    async def _async_update_data(self) -> ClimateConnectData:
        try:
            data = await self._async_fetch()
        except ClimateConnectAuthError as err:
            _LOGGER.debug("Update: credentials rejected (%s)", err)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except ClimateConnectUnexpectedPageError as err:
            self._handle_unexpected_page(err)
        except ClimateConnectError as err:
            _LOGGER.debug("Update failed (%s)", err)
            self._back_off()
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        self._failures = 0
        self._unexpected_pages = 0
        self.update_interval = self._base_interval
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        await self._async_import_energy(data)
        return data

    def _back_off(self) -> None:
        self._failures += 1
        self.update_interval = min(self._base_interval * 2**self._failures, MAX_BACKOFF)

    def _handle_unexpected_page(self, err: ClimateConnectUnexpectedPageError) -> None:
        """Report an unsupported sign-in page; stop polling once it keeps coming back.

        A single odd page can be a hiccup, so the first ones only fail the update.
        During setup this never stops the entry: Home Assistant retries it.
        """
        _LOGGER.debug("Update: %s", err)
        self._unexpected_pages += 1
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_UNEXPECTED_PAGE,
            translation_placeholders={
                "step": err.step,
                "title": self.config_entry.title,
            },
        )
        if self.data is not None and self._unexpected_pages >= UNEXPECTED_PAGE_LIMIT:
            self.update_interval = None
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="unexpected_page",
                translation_placeholders={"step": err.step},
            ) from err
        self._back_off()
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    async def _async_fetch(self) -> ClimateConnectData:
        now = dt_util.utcnow()
        slow = self._slow_due is None or now >= self._slow_due or not self.data
        if slow:
            self._boilers = await self.client.async_get_boilers()
        previous = self.data.boilers if self.data else {}
        data = ClimateConnectData()
        for info in self._boilers:
            try:
                boiler = await self._async_fetch_boiler(
                    info, previous.get(info.serial_number), slow=slow
                )
            except (ClimateConnectNotFoundError, ClimateConnectRequestError) as err:
                # A registered product the wifi API does not serve, for example.
                if info.serial_number not in self._skipped:
                    _LOGGER.info("Skipping a boiler the service refuses (%s)", err)
                    self._skipped.add(info.serial_number)
                continue
            self._skipped.discard(info.serial_number)
            self._apply_hold(boiler, now)
            data.boilers[info.serial_number] = boiler
        if slow:
            self._slow_due = now + SLOW_INTERVAL
        return data

    async def _async_fetch_boiler(
        self, info: BoilerInfo, old: BoilerData | None, *, slow: bool
    ) -> BoilerData:
        serial = info.serial_number
        status, mode = await asyncio.gather(
            self.client.async_get_device_status(serial),
            self.client.async_get_device_mode(serial),
        )
        vacation: VacationMode | None
        faults: list[Fault]
        tariff: Tariff | None
        if slow or old is None:
            if serial not in self._fault_texts:
                await self._async_fetch_fault_texts(serial)
            vacation, faults, tariff = await asyncio.gather(
                self.client.async_get_vacation_mode(serial),
                self.client.async_get_fault_history(serial),
                self.client.async_get_tariff(serial, dt_util.now().date()),
            )
        else:
            vacation, faults, tariff = old.vacation, old.faults, old.tariff
        return BoilerData(
            info=info,
            status=status,
            mode=mode,
            vacation=vacation,
            faults=faults,
            tariff=tariff,
            fault_texts=self._fault_texts.get(serial, {}),
            energy_days=self._energy_days.get(serial, []),
        )

    def _apply_hold(self, boiler: BoilerData, now: datetime) -> None:
        serial = boiler.info.serial_number
        hold = self._holds.get(serial)
        if hold is None:
            return
        hold.settle(boiler)
        if hold.empty or now >= hold.until:
            del self._holds[serial]
            return
        hold.apply(boiler)

    async def _async_fetch_fault_texts(self, serial: str) -> None:
        """Fetch the fault texts once; on failure, try again on the next slow update."""
        try:
            self._fault_texts[serial] = await self.client.async_get_fault_texts(serial)
        except ClimateConnectAuthError, ClimateConnectUnexpectedPageError:
            raise
        except ClimateConnectError as err:
            _LOGGER.debug("Fault texts unavailable (%s)", err)

    async def _async_import_energy(self, data: ClimateConnectData) -> None:
        """Import the energy history; on failure, try again on the next update."""
        now = dt_util.utcnow()
        if self._energy_due is not None and now < self._energy_due:
            return
        try:
            for serial, boiler in data.boilers.items():
                days = await self.client.async_get_energy_days(serial, now)
                self._energy_days[serial] = boiler.energy_days = days
                name = f"{boiler.info.model or DEFAULT_MODEL} {serial}"
                async_import_energy(self.hass, serial, name, days)
        except ClimateConnectAuthError, ClimateConnectUnexpectedPageError:
            # The next status update reports these.
            return
        except ClimateConnectError as err:
            _LOGGER.debug("Energy history unavailable (%s)", err)
            return
        self._energy_due = now + ENERGY_INTERVAL
