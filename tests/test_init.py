"""Setup, entities, actions, statistics and diagnostics."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.climateconnect import async_remove_config_entry_device
from custom_components.climateconnect.api import (
    ClimateConnectAuthError,
    ClimateConnectConnectionError,
    ClimateConnectNotFoundError,
    ClimateConnectRequestError,
    ClimateConnectUnexpectedPageError,
    DeviceMode,
    DeviceStatus,
    SignInStep,
)
from custom_components.climateconnect.const import DOMAIN
from custom_components.climateconnect.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .helpers import EMAIL, PASSWORD, SERIAL, load_json

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .fake_client import FakeClient

pytestmark = pytest.mark.usefixtures("mock_client")


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add (if needed) and set up the entry."""
    if entry.entry_id not in {e.entry_id for e in hass.config_entries.async_entries()}:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    """Return the entity id of a boiler entity by its key."""
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert found, f"{platform} {key} not registered"
    return found


def boiler_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """Return the boiler's device."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), entry.entry_id
    )
    assert device
    return device


def device_id(hass: HomeAssistant) -> str:
    """Return the boiler's device id."""
    return boiler_device(hass).id


async def test_setup_and_states(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The boiler gets a device and entities with the fetched values."""
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    device = boiler_device(hass)
    assert device.model == "Smart-upp boiler 80L Mono"
    assert device.serial_number == SERIAL
    assert device.sw_version == "13.61.133"

    def state(platform: str, key: str) -> Any:
        found = hass.states.get(entity_id(hass, platform, key))
        assert found
        return found

    assert state("sensor", "hot_water").state == "83.0"
    assert float(state("sensor", "power").state) == 0
    assert state("sensor", "power").attributes["unit_of_measurement"] == "W"
    assert state("sensor", "legionella_timer").state == "168"
    today = state("sensor", "energy_today")
    assert today.state == "9.8"
    assert today.attributes["costs"] == 2.25
    assert today.attributes["statistic_id"] == f"climateconnect:{SERIAL.lower()}_energy"
    yesterday = state("sensor", "energy_yesterday")
    assert yesterday.state == "8.6"
    assert yesterday.attributes["costs"] == 1.98
    assert state("sensor", "connection_state").state == "online"
    assert state("sensor", "tariff").state == "0.23"
    assert state("sensor", "tariff").attributes["start_date"] == "2025-06-17"
    last_fault = state("sensor", "last_fault")
    assert last_fault.state == "2026-10-27T07:33:00+00:00"
    assert last_fault.attributes["message"] == "Elementfout"
    assert last_fault.attributes["history"][0]["error_number"] == 7
    assert state("binary_sensor", "fault").state == "off"
    assert state("binary_sensor", "connected").state == "on"
    assert state("select", "mode").state == "continuous"
    assert state("number", "setpoint").state == "75.0"
    assert state("switch", "boost").state == "off"
    assert state("switch", "vacation_mode").state == "off"
    assert state("datetime", "vacation_start").state == "2026-12-23T17:00:00+00:00"
    assert state("datetime", "vacation_end").state == "2026-12-28T02:00:00+00:00"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_fault_present(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """A reported error number turns the fault sensor on, with its texts."""
    raw = load_json("device_status.json")["result"] | {"errorNumber": 7}
    client.status = DeviceStatus.from_api(raw)
    await setup(hass, config_entry)
    fault = hass.states.get(entity_id(hass, "binary_sensor", "fault"))
    assert fault
    assert fault.state == "on"
    assert fault.attributes["message"] == "Elementfout"
    assert fault.attributes["description"] == "Element wordt niet warm."


async def test_mode_select(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Selecting a mode keeps the setpoint or schedule the boiler has."""
    await setup(hass, config_entry)
    select = entity_id(hass, "select", "mode")
    schedule = client.mode.schedule

    for option in ("schedule", "continuous", "holiday", "smart_control"):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": select, "option": option},
            blocking=True,
        )

    calls = [call for call in client.calls if call[0] == "set_device_mode"]
    assert calls[0][1][1] is DeviceMode.SCHEDULE
    assert calls[0][2]["schedule"] == schedule
    assert calls[1][1][1] is DeviceMode.CONTINUOUS
    assert calls[1][2]["temperature"] == 75.0
    assert calls[2][2] == {"temperature": None, "schedule": None}
    assert calls[3][1][1] is DeviceMode.SMART_CONTROL


async def test_setpoint(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Setting the setpoint selects Continuous mode with that temperature."""
    await setup(hass, config_entry)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, "number", "setpoint"), "value": 65},
        blocking=True,
    )
    assert client.calls[-1] == (
        "set_device_mode",
        (SERIAL, DeviceMode.CONTINUOUS),
        {"temperature": 65.0, "schedule": None},
    )


async def test_boost(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """The boost switch starts and stops boosting."""
    await setup(hass, config_entry)
    boost = entity_id(hass, "switch", "boost")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": boost}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": boost}, blocking=True
    )
    assert [call[2] for call in client.calls] == [{"active": True}, {"active": False}]


async def test_vacation(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Vacation mode is switched with the stored period; dates can be changed."""
    await setup(hass, config_entry)
    switch = entity_id(hass, "switch", "vacation_mode")
    start = entity_id(hass, "datetime", "vacation_start")
    end = entity_id(hass, "datetime", "vacation_end")

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    assert client.calls[-1][2]["active"] is True
    assert client.calls[-1][2]["start"] == datetime(2026, 12, 23, 17, tzinfo=UTC)
    state = hass.states.get(switch)
    assert state
    assert state.state == "on"

    # A start after the current end moves the end a week later.
    await hass.services.async_call(
        "datetime",
        "set_value",
        {"entity_id": start, "datetime": "2027-01-10 08:00:00"},
        blocking=True,
    )
    sent = client.calls[-1][2]
    assert sent["active"] is True
    assert sent["end"] - sent["start"] == timedelta(days=7)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "datetime",
            "set_value",
            {"entity_id": end, "datetime": "2027-01-01 08:00:00"},
            blocking=True,
        )

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch}, blocking=True
    )
    assert client.calls[-1][2]["active"] is False


async def test_vacation_in_past(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Turning on a period that already ended is refused."""
    past = dt_util.utcnow() - timedelta(days=10)
    client.vacation = type(client.vacation)(
        active=False, start=past, end=past + timedelta(days=2)
    )
    await setup(hass, config_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id(hass, "switch", "vacation_mode")},
            blocking=True,
        )


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (
            ClimateConnectRequestError("vacation-mode: Measurement not found"),
            ServiceValidationError,
        ),
        (ClimateConnectConnectionError("timeout"), HomeAssistantError),
    ],
)
async def test_write_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    client: FakeClient,
    error: Exception,
    raised: type[Exception],
) -> None:
    """Refused and failed writes are reported to the user."""
    await setup(hass, config_entry)

    async def fail(*_args: Any, **_kwargs: Any) -> None:
        raise error

    with patch.object(client, "async_set_boost", fail), pytest.raises(raised):
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id(hass, "switch", "boost")},
            blocking=True,
        )


async def test_schedule_actions(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """get_schedule returns what set_schedule takes; set_schedule sends it."""
    await setup(hass, config_entry)
    boiler = device_id(hass)

    response = await hass.services.async_call(
        DOMAIN,
        "get_schedule",
        {"device_id": boiler},
        blocking=True,
        return_response=True,
    )
    assert response == {
        "mode": "Continuous",
        "schedule": {
            "monday": [
                {"time": "19:00", "temperature": 75.0},
                {"time": "20:00", "temperature": 65.0},
            ],
            "tuesday": [
                {"time": "17:00", "temperature": 70.0},
                {"time": "20:00", "temperature": 75.0},
            ],
            "wednesday": [{"time": "17:00", "temperature": 60.0}],
            "thursday": [],
            "friday": [],
            "saturday": [],
            "sunday": [],
        },
    }

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "device_id": boiler,
            "schedule": {
                "monday": [{"time": "06:30", "temperature": 65}],
                "sunday": [{"time": "08:00:00", "temperature": "70"}],
            },
        },
        blocking=True,
    )
    name, args, kwargs = client.calls[-1]
    assert (name, args) == ("set_device_mode", (SERIAL, DeviceMode.SCHEDULE))
    assert kwargs["schedule"] == {
        0: {23400: 65.0},
        1: {},
        2: {},
        3: {},
        4: {},
        5: {},
        6: {28800: 70.0},
    }


async def test_schedule_action_validation(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Out-of-range temperatures and unknown devices are refused."""
    await setup(hass, config_entry)
    with pytest.raises(Exception, match="temperature"):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {
                "device_id": device_id(hass),
                "schedule": {"monday": [{"time": "06:30", "temperature": 95}]},
            },
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "get_schedule",
            {"device_id": "missing"},
            blocking=True,
            return_response=True,
        )


async def test_set_tariff(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """set_tariff sends the price, from today unless a date is given."""
    await setup(hass, config_entry)
    await hass.services.async_call(
        DOMAIN,
        "set_tariff",
        {"device_id": device_id(hass), "price": 0.2512},
        blocking=True,
    )
    assert client.calls[-1] == ("set_tariff", (0.2512, dt_util.now().date()), {})
    await hass.services.async_call(
        DOMAIN,
        "set_tariff",
        {"device_id": device_id(hass), "price": 0.3, "start_date": "2027-01-01"},
        blocking=True,
    )
    assert client.calls[-1] == ("set_tariff", (0.3, date(2027, 1, 1)), {})


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Rejected credentials start a reauthentication flow."""
    client.fail_with = ClimateConnectAuthError("rejected")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_unexpected_page_creates_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """An unsupported sign-in page stops updates and raises a repair issue."""
    await setup(hass, config_entry)
    client.fail_with = ClimateConnectUnexpectedPageError(SignInStep.CONFIRM)
    coordinator = config_entry.runtime_data
    issue_id = f"unexpected_sign_in_page_{config_entry.entry_id}"

    # The first odd pages only fail the update; the third in a row stops polling.
    await coordinator.async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert coordinator.update_interval is not None
    await coordinator.async_refresh()
    assert coordinator.update_interval is not None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.update_interval is None

    client.fail_with = None
    await coordinator.async_refresh()
    assert not ir.async_get(hass).async_get_issue(
        DOMAIN, f"unexpected_sign_in_page_{config_entry.entry_id}"
    )


async def test_connection_error_backs_off(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Failed updates make entities unavailable and lengthen the interval."""
    await setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    client.fail_with = ClimateConnectConnectionError("timeout")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, "sensor", "hot_water"))
    assert state
    assert state.state == "unavailable"
    assert coordinator.update_interval == timedelta(minutes=10)

    client.fail_with = None
    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(minutes=5)


async def test_energy_statistics(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Energy use and costs are imported with running sums."""
    with patch(
        "custom_components.climateconnect.statistics.async_add_external_statistics"
    ) as add:
        await setup(hass, config_entry)
    energy, costs = add.call_args_list
    assert energy.args[1]["statistic_id"] == f"climateconnect:{SERIAL.lower()}_energy"
    assert energy.args[1]["unit_of_measurement"] == "kWh"
    assert [row["sum"] for row in energy.args[2]] == pytest.approx([8.6, 18.4])
    assert energy.args[2][0]["start"] == client.energy[0].start
    assert costs.args[1]["statistic_id"] == f"climateconnect:{SERIAL.lower()}_costs"
    assert [row["sum"] for row in costs.args[2]] == pytest.approx([1.98, 4.23])


async def test_energy_failure_does_not_break_update(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Without energy history the entities still load."""
    client.fail_energy_with = ClimateConnectConnectionError("timeout")
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED


async def test_diagnostics(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Diagnostics leave out the e-mail address, password and serial number."""
    await setup(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    text = str(diagnostics)
    assert EMAIL not in text
    assert PASSWORD not in text
    assert SERIAL not in text
    boiler = diagnostics["boilers"][0]
    assert boiler["boiler"] == "boiler_1"
    assert boiler["status"]["boilerContent"] == 0.83
    assert boiler["faults"][0]["error_number"] == 7


async def test_unexpected_page_during_setup_retries(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """An odd sign-in page while setting up makes Home Assistant retry later."""
    client.fail_with = ClimateConnectUnexpectedPageError(SignInStep.CALLBACK)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_power_in_watts(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """The API's kW are shown in W."""
    raw = load_json("device_status.json")["result"] | {"devicePowerMeasured": 1.06}
    client.status = DeviceStatus.from_api(raw)
    await setup(hass, config_entry)
    state = hass.states.get(entity_id(hass, "sensor", "power"))
    assert state
    assert float(state.state) == 1060


async def test_changes_held_until_reported(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """A change shows at once and survives reads that still return the old value."""
    await setup(hass, config_entry)
    boost = entity_id(hass, "switch", "boost")
    mode = entity_id(hass, "select", "mode")
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": boost}, blocking=True
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": mode, "option": "holiday"},
        blocking=True,
    )
    coordinator = config_entry.runtime_data

    # The boiler still reports the old values.
    await coordinator.async_refresh()
    assert hass.states.get(boost).state == "on"  # type: ignore[union-attr]
    assert hass.states.get(mode).state == "holiday"  # type: ignore[union-attr]

    # Once it reports the new mode, the hold for the mode ends.
    client.status = DeviceStatus.from_api(
        load_json("device_status.json")["result"] | {"deviceMode": "Holiday"}
    )
    await coordinator.async_refresh()
    client.status = DeviceStatus.from_api(load_json("device_status.json")["result"])
    await coordinator.async_refresh()
    assert hass.states.get(mode).state == "continuous"  # type: ignore[union-attr]
    assert hass.states.get(boost).state == "on"  # type: ignore[union-attr]

    # After the hold time the boiler's own value shows again.
    with patch(
        "custom_components.climateconnect.coordinator.dt_util.utcnow",
        return_value=dt_util.utcnow() + timedelta(minutes=2),
    ):
        await coordinator.async_refresh()
    assert hass.states.get(boost).state == "off"  # type: ignore[union-attr]


async def test_continuous_without_setpoint(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Continuous mode without a known setpoint points at the Setpoint entity."""
    client.mode = type(client.mode)(
        mode=DeviceMode.HOLIDAY, temperature=None, schedule={}
    )
    await setup(hass, config_entry)
    calls = len(client.calls)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id(hass, "select", "mode"), "option": "continuous"},
            blocking=True,
        )
    assert err.value.translation_key == "setpoint_unknown"
    assert err.value.translation_placeholders == {
        "entity_id": entity_id(hass, "number", "setpoint")
    }
    assert len(client.calls) == calls


async def test_boiler_refused_is_skipped(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """A boiler the wifi API refuses does not break the other boilers."""
    client.boilers = [
        *client.boilers,
        type(client.boilers[0])(
            serial_number="GRB000000002",
            model=None,
            item_code=None,
            installation_date=None,
        ),
    ]
    client.fail_status_for["GRB000000002"] = ClimateConnectNotFoundError("status")
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert list(config_entry.runtime_data.data.boilers) == [SERIAL]


async def test_fault_texts_retried(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Fault texts that failed to load are fetched again on the next slow update."""
    client.fail_fault_texts_with = ClimateConnectConnectionError("timeout")
    await setup(hass, config_entry)
    coordinator = config_entry.runtime_data
    assert coordinator.data.boilers[SERIAL].fault_texts == {}

    client.fail_fault_texts_with = None
    coordinator.request_slow_refresh()
    await coordinator.async_refresh()
    assert coordinator.data.boilers[SERIAL].fault_texts == client.fault_texts


async def test_remove_device(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """A boiler's device can be removed only once the account no longer lists it."""
    await setup(hass, config_entry)
    device = boiler_device(hass)
    assert not await async_remove_config_entry_device(hass, config_entry, device)
    client.boilers = []
    config_entry.runtime_data.request_slow_refresh()
    await config_entry.runtime_data.async_refresh()
    assert await async_remove_config_entry_device(hass, config_entry, device)


async def test_unload_removes_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Unloading the entry removes its repair issue."""
    await setup(hass, config_entry)
    client.fail_with = ClimateConnectUnexpectedPageError(SignInStep.CONFIRM)
    await config_entry.runtime_data.async_refresh()
    issue_id = f"unexpected_sign_in_page_{config_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert not ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


async def test_removed_energy_sensor_is_cleaned_up(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The energy consumption sensor of earlier versions is removed."""
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{SERIAL}_energy_consumption",
        config_entry=config_entry,
    )
    await setup(hass, config_entry)
    assert registry.async_get(old.entity_id) is None


async def test_energy_today_without_row(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Without a row for today the sensor is unknown, not zero."""
    client.energy = client.energy[:1]
    await setup(hass, config_entry)
    state = hass.states.get(entity_id(hass, "sensor", "energy_today"))
    assert state
    assert state.state == "unknown"
    assert state.attributes["costs"] is None
