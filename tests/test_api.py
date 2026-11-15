"""Client tests: sign-in, session renewal, requests and parsing."""

# The renewal tests drive the client's session handling directly.
# pylint: disable=protected-access

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.climateconnect.api import (
    APP_REDIRECT,
    B2C_HOST,
    BETTY_BASE,
    WIFI_API,
    ClimateConnectAuthError,
    ClimateConnectClient,
    ClimateConnectConnectionError,
    ClimateConnectNotFoundError,
    ClimateConnectRequestError,
    ClimateConnectUnexpectedPageError,
    DeviceMode,
    DeviceState,
    SignInStep,
    clean_serial,
    format_vacation_datetime,
    jwt_claims,
    parse_schedule,
    schedule_to_api,
)

from .helpers import (
    EMAIL,
    PASSWORD,
    REFRESH_TOKEN,
    SERIAL,
    STATE,
    load_json,
    load_text,
    make_token,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

TENANT = "/climateforlifeportal.onmicrosoft.com/B2C_1_SignUpandSignIn"
AUTHORIZE_URL = (
    f"{B2C_HOST}/climateforlifeportal.onmicrosoft.com/oauth2/v2.0/authorize"
    "?p=B2C_1_SignUpandSignIn&client_id=test&state=test"
)
CALLBACK_URL = "https://services.climateforlife.nl/sso/callback"
TOKEN = make_token()


def mock_sign_in(
    aioclient_mock: AiohttpClientMocker,
    *,
    credentials_reply: str = '{"status":"200"}',
    confirmed_page: str | None = None,
    token: str = TOKEN,
) -> None:
    """Register the responses of a complete sign-in."""
    aioclient_mock.get(f"{BETTY_BASE}/sso/initiate", json={"sso": AUTHORIZE_URL})
    aioclient_mock.get(AUTHORIZE_URL, text=load_text("authorize.html"))
    aioclient_mock.post(f"{B2C_HOST}{TENANT}/SelfAsserted", text=credentials_reply)
    aioclient_mock.get(
        f"{B2C_HOST}{TENANT}/api/CombinedSigninAndSignup/confirmed",
        text=confirmed_page or load_text("confirmed.html"),
    )
    aioclient_mock.post(
        CALLBACK_URL,
        status=302,
        headers={"Location": f"{APP_REDIRECT}?token={token}"},
    )


def create_client(hass: HomeAssistant) -> ClimateConnectClient:
    """Return a client on the mocked session."""
    return ClimateConnectClient(async_create_clientsession(hass), EMAIL, PASSWORD)


def calls_to(aioclient_mock: AiohttpClientMocker, fragment: str) -> list[Any]:
    """Return the recorded requests whose URL contains `fragment`."""
    return [call for call in aioclient_mock.mock_calls if fragment in str(call[1])]


async def test_sign_in(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The sign-in walks B2C and returns the account from the token."""
    mock_sign_in(aioclient_mock)
    client = create_client(hass)

    assert await client.async_sign_in() == EMAIL
    assert client.account_id == EMAIL
    assert client.token_expires == datetime(2027, 11, 15, 15, 45, 24, tzinfo=UTC)

    initiate = calls_to(aioclient_mock, "/sso/initiate")[0]
    assert initiate[1].query["application_id"] == "2da3d256-ca0a-4041-84ba-93856efceef9"
    assert initiate[1].query["redirect"] == APP_REDIRECT
    credentials = calls_to(aioclient_mock, "SelfAsserted")[0]
    assert credentials[1].query["tx"] == "StateProperties=test-transaction"
    assert credentials[2] == {
        "request_type": "RESPONSE",
        "logonIdentifier": EMAIL,
        "password": PASSWORD,
    }
    assert credentials[3]["X-CSRF-TOKEN"] == "test-csrf-token"
    callback = calls_to(aioclient_mock, "/sso/callback")[0]
    assert callback[2] == {"state": "test-state", "code": "test-code+1"}


async def test_sign_in_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """B2C status 400 means wrong credentials."""
    mock_sign_in(aioclient_mock, credentials_reply='{"status":"400"}')
    with pytest.raises(ClimateConnectAuthError):
        await create_client(hass).async_sign_in()


@pytest.mark.parametrize(
    ("kwargs", "step"),
    [
        ({"credentials_reply": "<html></html>"}, SignInStep.CREDENTIALS),
        ({"credentials_reply": '{"status":"500"}'}, SignInStep.CREDENTIALS),
        ({"confirmed_page": "<html>MFA</html>"}, SignInStep.CONFIRM),
        ({"token": "not-a-jwt"}, SignInStep.CALLBACK),
    ],
)
async def test_sign_in_unexpected_page(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, str],
    step: SignInStep,
) -> None:
    """An unknown page stops the sign-in with the step it happened at."""
    mock_sign_in(aioclient_mock, **kwargs)
    with pytest.raises(ClimateConnectUnexpectedPageError) as err:
        await create_client(hass).async_sign_in()
    assert err.value.step == step


async def test_sign_in_unexpected_authorize_page(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An authorize page without SETTINGS is unexpected."""
    aioclient_mock.get(f"{BETTY_BASE}/sso/initiate", json={"sso": AUTHORIZE_URL})
    aioclient_mock.get(AUTHORIZE_URL, text="<html>maintenance</html>")
    with pytest.raises(ClimateConnectUnexpectedPageError) as err:
        await create_client(hass).async_sign_in()
    assert err.value.step == SignInStep.AUTHORIZE


async def test_sign_in_initiate_failures(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A server error is a connection error; a strange answer an unexpected page."""
    aioclient_mock.get(f"{BETTY_BASE}/sso/initiate", status=502)
    with pytest.raises(ClimateConnectConnectionError):
        await create_client(hass).async_sign_in()

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BETTY_BASE}/sso/initiate", json={"sso": "https://evil.test"})
    with pytest.raises(ClimateConnectUnexpectedPageError):
        await create_client(hass).async_sign_in()


async def test_wifi_request_uses_bearer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Wifi API calls carry the token; back end calls the session headers."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.get(
        f"{WIFI_API}/GetDeviceStatus", json=load_json("device_status.json")
    )
    aioclient_mock.get(
        f"{BETTY_BASE}/api/consumer-app/device", json=load_json("devices.json")
    )
    client = create_client(hass)

    status = await client.async_get_device_status(SERIAL)
    boilers = await client.async_get_boilers()

    assert status.device_state is DeviceState.ONLINE
    assert status.device_mode is DeviceMode.CONTINUOUS
    assert status.boiler_content == 0.83
    assert status.legionella_prevention_timer == 168
    assert status.software_version == "13.61.133"
    assert status.error_number is None
    assert boilers[0].serial_number == SERIAL
    assert boilers[0].model == "Smart-upp boiler 80L Mono"
    assert boilers[0].installation_date == date(2025, 2, 20)

    wifi = calls_to(aioclient_mock, "GetDeviceStatus")[0]
    assert wifi[1].query["serialNumber"] == SERIAL
    assert wifi[3]["Authorization"] == f"Bearer {TOKEN}"
    betty = calls_to(aioclient_mock, "consumer-app/device")[0]
    assert betty[3]["state"] == STATE
    assert betty[3]["application-id"] == "2da3d256-ca0a-4041-84ba-93856efceef9"


async def test_session_renewed_after_401(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected token is renewed through session/validate and the call retried."""
    mock_sign_in(aioclient_mock)
    renewed = make_token(
        refresh_token=REFRESH_TOKEN, expires_at="2028-01-01T00:00:00+00:00"
    )
    aioclient_mock.get(f"{BETTY_BASE}/sso/session/validate", json=renewed)
    responses = iter([401, 200])

    async def status_side_effect(*_args: Any) -> Any:
        status = next(responses)
        return _response(status, load_json("device_status.json"))

    aioclient_mock.get(f"{WIFI_API}/GetDeviceStatus", side_effect=status_side_effect)
    client = create_client(hass)

    await client.async_get_device_status(SERIAL)

    validate = calls_to(aioclient_mock, "session/validate")[0]
    assert validate[1].query["refresh_token"] == REFRESH_TOKEN
    assert validate[1].query["state"] == STATE
    assert calls_to(aioclient_mock, "GetDeviceStatus")[1][3]["Authorization"] == (
        f"Bearer {renewed}"
    )
    assert client.token_expires == datetime(2028, 1, 1, tzinfo=UTC)


async def test_session_renewal_refused_signs_in_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """When renewal is refused, the client signs in with the password again."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.get(f"{BETTY_BASE}/sso/session/validate", status=422)
    aioclient_mock.get(
        f"{WIFI_API}/GetDeviceStatus", json=load_json("device_status.json")
    )
    client = create_client(hass)
    await client.async_sign_in()

    with patch("custom_components.climateconnect.api.TOKEN_RENEW_AFTER", timedelta(0)):
        await client.async_get_device_status(SERIAL)

    assert len(calls_to(aioclient_mock, "/sso/initiate")) == 2


async def test_rejected_after_renewal_signs_in_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A token still rejected after renewal leads to a new sign-in, then gives up."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.get(f"{BETTY_BASE}/sso/session/validate", json=make_token())
    aioclient_mock.get(f"{WIFI_API}/GetDeviceStatus", status=401)
    with pytest.raises(ClimateConnectConnectionError):
        await create_client(hass).async_get_device_status(SERIAL)
    assert len(calls_to(aioclient_mock, "session/validate")) == 1
    assert len(calls_to(aioclient_mock, "/sso/initiate")) == 2
    assert len(calls_to(aioclient_mock, "GetDeviceStatus")) == 3


async def test_parallel_rejections_renew_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Calls refused with a token another call already replaced just retry."""
    mock_sign_in(aioclient_mock)
    renewed = make_token(expires_at="2028-01-01T00:00:00+00:00")
    aioclient_mock.get(f"{BETTY_BASE}/sso/session/validate", json=renewed)

    client = create_client(hass)
    await client.async_sign_in()
    old_token = client._session.token  # type: ignore[union-attr]
    first = await client._async_session(rejected=old_token)
    second = await client._async_session(rejected=old_token)
    assert first is second
    assert len(calls_to(aioclient_mock, "session/validate")) == 1


async def test_renewal_client_error_signs_in_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Any 4xx from session/validate falls back to a full sign-in."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.get(f"{BETTY_BASE}/sso/session/validate", status=400)
    client = create_client(hass)
    await client.async_sign_in()
    await client._async_session(rejected=client._session.token)  # type: ignore[union-attr]
    assert len(calls_to(aioclient_mock, "/sso/initiate")) == 2


async def test_transient_sign_in_failures(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A maintenance page or a server error during sign-in is worth retrying."""
    aioclient_mock.get(f"{BETTY_BASE}/sso/initiate", text="<html>Onderhoud</html>")
    with pytest.raises(ClimateConnectConnectionError):
        await create_client(hass).async_sign_in()

    aioclient_mock.clear_requests()
    mock_sign_in(aioclient_mock)
    aioclient_mock._mocks = [
        mock for mock in aioclient_mock._mocks if "/sso/callback" not in str(mock._url)
    ]
    aioclient_mock.post(CALLBACK_URL, status=503)
    with pytest.raises(ClimateConnectConnectionError):
        await create_client(hass).async_sign_in()


async def test_tariff_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A plain-text answer to a tariff change is a refusal."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.post(f"{BETTY_BASE}/api/consumer-app/tariff", text="Invalid date")
    with pytest.raises(ClimateConnectRequestError, match="Invalid date"):
        await create_client(hass).async_set_tariff(0.25, date(2026, 11, 15))


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (404, "", ClimateConnectNotFoundError),
        (400, '{"message": {"message": "Invalid mode"}}', ClimateConnectRequestError),
        (500, "", ClimateConnectConnectionError),
        (200, "not json", ClimateConnectRequestError),
    ],
)
async def test_request_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    body: str,
    error: type[Exception],
) -> None:
    """HTTP errors map to the client's errors."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.get(f"{WIFI_API}/GetDeviceMode", status=status, text=body)
    with pytest.raises(error):
        await create_client(hass).async_get_device_mode(SERIAL)


async def test_network_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A timeout is a connection error."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.get(f"{WIFI_API}/GetDeviceMode", exc=TimeoutError)
    with pytest.raises(ClimateConnectConnectionError):
        await create_client(hass).async_get_device_mode(SERIAL)


async def test_writes(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Mode, boost, vacation and tariff are sent as the app sends them."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.post(f"{WIFI_API}/UpdateDeviceMode", status=204)
    aioclient_mock.post(f"{WIFI_API}/BoostBoiler", status=204)
    aioclient_mock.post(
        f"{BETTY_BASE}/api/consumer-app/device/{SERIAL}/vacation-mode",
        json={"mode": "Aan", "message": "Vakantiemodus is nu ingeschakeld"},
    )
    aioclient_mock.post(
        f"{BETTY_BASE}/api/consumer-app/tariff",
        text='"tariffs": [{"tariff": 0.25, "start_date": "2026-11-15"}]',
    )
    client = create_client(hass)
    cet = timezone(timedelta(hours=1))

    await client.async_set_device_mode(SERIAL, DeviceMode.CONTINUOUS, temperature=65.0)
    await client.async_set_device_mode(
        SERIAL, DeviceMode.SCHEDULE, schedule={0: {68400: 75.0}}
    )
    await client.async_set_boost(SERIAL, active=True)
    await client.async_set_vacation_mode(
        SERIAL,
        active=True,
        start=datetime(2026, 12, 23, 18, tzinfo=cet),
        end=datetime(2026, 12, 28, 3, tzinfo=cet),
    )
    await client.async_set_tariff(0.25, date(2026, 11, 15))

    modes = calls_to(aioclient_mock, "UpdateDeviceMode")
    assert modes[0][2] == {
        "serialNumber": SERIAL,
        "deviceMode": "Continuous",
        "temperature": 65,
    }
    assert modes[1][2]["schedule"]["0"] == {"68400": 75}
    assert modes[1][2]["schedule"]["6"] == {}
    assert calls_to(aioclient_mock, "BoostBoiler")[0][2] == {
        "activateBoost": True,
        "serialNumber": SERIAL,
    }
    assert calls_to(aioclient_mock, "vacation-mode")[0][2] == {
        "startdate": "2026-12-23T18:00:00.000+01:00",
        "mode": "Aan",
        "enddate": "2026-12-28T03:00:00.000+01:00",
    }
    assert calls_to(aioclient_mock, "consumer-app/tariff")[0][2] == {
        "tariff": {"tariff": 0.25, "start_date": "15-11-2026"}
    }


async def test_vacation_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The back end refuses vacation mode with HTTP 200 and plain text."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.post(
        f"{BETTY_BASE}/api/consumer-app/device/{SERIAL}/vacation-mode",
        text="Measurement not found",
    )
    now = datetime(2026, 12, 1, tzinfo=UTC)
    with pytest.raises(ClimateConnectRequestError, match="Measurement not found"):
        await create_client(hass).async_set_vacation_mode(
            SERIAL, active=True, start=now, end=now + timedelta(days=1)
        )


async def test_reads(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Vacation, tariff, faults, fault texts and energy are parsed."""
    mock_sign_in(aioclient_mock)
    device = f"{BETTY_BASE}/api/consumer-app/device/{SERIAL}"
    aioclient_mock.get(f"{device}/get-vacation-mode", json=load_json("vacation.json"))
    aioclient_mock.get(f"{device}/tariff", json=load_json("tariff.json"))
    aioclient_mock.get(
        f"{device}/malfunction-code-translation", json=load_json("fault_texts.json")
    )
    aioclient_mock.get(
        f"{WIFI_API}/GetFaultHistory", json=load_json("fault_history.json")
    )
    aioclient_mock.get(
        f"{WIFI_API}/GetEnergyConsumption", json=load_json("energy_day.json")
    )
    aioclient_mock.get(f"{WIFI_API}/GetDeviceMode", json=load_json("device_mode.json"))
    client = create_client(hass)
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)

    vacation = await client.async_get_vacation_mode(SERIAL)
    tariff = await client.async_get_tariff(SERIAL, date(2026, 11, 15))
    texts = await client.async_get_fault_texts(SERIAL)
    faults = await client.async_get_fault_history(SERIAL)
    days = await client.async_get_energy_days(SERIAL, now)
    mode = await client.async_get_device_mode(SERIAL)

    assert vacation.active is False
    assert vacation.start == datetime(2026, 12, 23, 17, tzinfo=UTC)
    assert tariff.price == 0.23
    assert tariff.start == date(2025, 6, 17)
    assert texts[7].message == "Elementfout"
    assert [fault.error_number for fault in faults] == [7, 4]
    assert faults[0].duration == 3600
    assert [day.start.day for day in days] == [13, 14, 15]
    assert days[0].consumption == pytest.approx(8.6417, rel=1e-3)
    assert mode.temperature == 75.0
    assert mode.schedule[0] == {68400: 75.0, 72000: 65.0}
    energy = calls_to(aioclient_mock, "GetEnergyConsumption")[0][1].query
    assert energy["interval"] == "Day"
    assert energy["endDate"] == str(int(now.timestamp() * 1000))


def test_helpers() -> None:
    """Serials, schedules, dates and tokens are converted as the services expect."""
    assert clean_serial(" #grb000000001 ") == SERIAL
    assert format_vacation_datetime(
        datetime(2026, 12, 23, 18, 0, 5, 123456, tzinfo=UTC)
    ) == ("2026-12-23T18:00:05.000+00:00")
    schedule = parse_schedule({"0": {"68400": 75, "bad": 1}, "9": {}, "x": {}, "1": []})
    assert schedule[0] == {68400: 75.0}
    assert schedule_to_api(schedule)["0"] == {"68400": 75}
    assert schedule_to_api({2: {3600: 62.5}})["2"] == {"3600": 62.5}
    assert jwt_claims("nonsense") == {}
    assert jwt_claims(TOKEN)["session"]["state"] == STATE


def _response(status: int, body: Any) -> Any:
    return AiohttpClientMockResponse("GET", URL(WIFI_API), status=status, json=body)
