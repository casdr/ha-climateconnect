"""The Itho Daalderop ClimateConnect integration: wifi-connected boilers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ClimateConnectClient, create_cookie_jar
from .const import DOMAIN
from .coordinator import ClimateConnectConfigEntry, ClimateConnectCoordinator
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry
    from homeassistant.helpers.typing import ConfigType

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)  # pylint: disable=invalid-name


def create_client(
    hass: HomeAssistant, email: str, password: str
) -> ClimateConnectClient:
    """Create a client with its own cookie jar, detached when the entry unloads."""
    session = async_create_clientsession(hass, cookie_jar=create_cookie_jar())
    return ClimateConnectClient(session, email, password)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register the integration's actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ClimateConnectConfigEntry
) -> bool:
    """Set up ClimateConnect from a config entry."""
    client = create_client(hass, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = ClimateConnectCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ClimateConnectConfigEntry
) -> bool:
    """Unload a config entry."""
    ir.async_delete_issue(hass, DOMAIN, entry.runtime_data.issue_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    _hass: HomeAssistant,
    entry: ClimateConnectConfigEntry,
    device: DeviceEntry,
) -> bool:
    """Allow removing a boiler's device once the account no longer lists it."""
    return not any(
        domain == DOMAIN and serial in entry.runtime_data.data.boilers
        for domain, serial in device.identifiers
    )
