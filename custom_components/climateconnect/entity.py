"""Base entity: one device per boiler, identified by its serial number."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    ClimateConnectAuthError,
    ClimateConnectError,
    ClimateConnectNotFoundError,
    ClimateConnectRequestError,
)
from .const import DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import ClimateConnectCoordinator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .coordinator import ClimateConnectConfigEntry
    from .models import BoilerData


def device_identifier(serial: str) -> tuple[str, str]:
    """Device registry identifier of a boiler."""
    return (DOMAIN, serial)


async def async_call_api(call: Awaitable[None]) -> None:
    """Await a write to the boiler and turn client errors into user-facing errors."""
    try:
        await call
    except ClimateConnectRequestError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="request_refused",
            translation_placeholders={"error": str(err)},
        ) from err
    except ClimateConnectNotFoundError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="boiler_not_found"
        ) from err
    except ClimateConnectAuthError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except ClimateConnectError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err


class ClimateConnectEntity(CoordinatorEntity[ClimateConnectCoordinator]):
    """An entity of one boiler."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ClimateConnectCoordinator, serial: str, key: str
    ) -> None:
        """Initialize the entity.

        Args:
        ----
            coordinator: The coordinator.
            serial: The boiler's serial number.
            key: Added to the unique ID, to tell the boiler's entities apart.

        """
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_{key}"
        boiler = coordinator.data.boilers[serial]
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier(serial)},
            name=boiler.info.model or DEFAULT_MODEL,
            manufacturer=MANUFACTURER,
            model=boiler.info.model or DEFAULT_MODEL,
            model_id=boiler.info.item_code,
            serial_number=serial,
            sw_version=boiler.status.software_version,
        )

    @property
    def serial(self) -> str:
        """The boiler's serial number."""
        return self._serial

    @property
    def boiler(self) -> BoilerData | None:
        """The boiler as last read, None when the account no longer lists it."""
        return self.coordinator.data.boilers.get(self._serial)

    @property
    def available(self) -> bool:
        """Available while the last update succeeded and still lists this boiler."""
        return super().available and self.boiler is not None

    async def async_write_and_hold(self, call: Awaitable[None], **values: Any) -> None:
        """Send a change and show its values until the boiler reports them."""
        await async_call_api(call)
        self.coordinator.async_hold(self._serial, **values)


def async_add_boiler_entities(
    entry: ClimateConnectConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    create: Callable[[ClimateConnectCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every boiler now, and for boilers that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_boilers() -> None:
        new = [serial for serial in coordinator.data.boilers if serial not in known]
        known.update(new)
        async_add_entities(
            entity for serial in new for entity in create(coordinator, serial)
        )

    add_new_boilers()
    entry.async_on_unload(coordinator.async_add_listener(add_new_boilers))
