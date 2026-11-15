"""Async client for Itho Daalderop ClimateConnect boilers.

Two cloud services are involved:

* The wifi API (https://wifi-api.id-c.net/api, documented in its swagger.json) reads
  and controls the boiler. It takes the account's JWT as bearer token.
* The app's Betty Blocks back end (itho-tussenlaag.bettywebblocks.com) lists the
  account's boilers and keeps vacation mode, tariffs and fault texts. It identifies
  the session by the `application-id` and `state` headers and a session cookie.

Signing in works without a browser: `/sso/initiate` returns the Azure AD B2C
authorize URL, the credentials go to B2C's SelfAsserted endpoint, the confirm page
posts the authorization code to the SSO callback, and the callback redirects to
`climateconnect://login?token=<JWT>`. `/sso/session/validate` renews the JWT.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from html import unescape
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import aiohttp

if TYPE_CHECKING:
    from types import TracebackType

_LOGGER = logging.getLogger(__name__)

WIFI_API = "https://wifi-api.id-c.net/api"
BETTY_BASE = "https://itho-tussenlaag.bettywebblocks.com"
APPLICATION_ID = "2da3d256-ca0a-4041-84ba-93856efceef9"
APP_REDIRECT = "climateconnect://login"
B2C_HOST = "https://climateforlifeportal.b2clogin.com"
EXPECTED_SIGN_IN_API = "CombinedSigninAndSignup"
SSO_CALLBACK_HOST = "services.climateforlife.nl"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# The JWT is valid for a year; the app renews it every time it opens. Renew daily.
TOKEN_RENEW_AFTER = timedelta(hours=24)
# Energy history starts this long ago: the app asks for the same window.
HISTORY_WINDOW = timedelta(days=3 * 365)


class SignInStep(StrEnum):
    """Steps of the sign-in, named in errors about an unexpected page."""

    INITIATE = "initiate"
    AUTHORIZE = "authorize"
    CREDENTIALS = "credentials"
    CONFIRM = "confirm"
    CALLBACK = "callback"


class DeviceMode(StrEnum):
    """Operating modes of the boiler."""

    SMART_CONTROL = "SmartControl"
    SCHEDULE = "Schedule"
    CONTINUOUS = "Continuous"
    HOLIDAY = "Holiday"


class DeviceState(StrEnum):
    """Connection states the wifi API reports."""

    UNKNOWN = "Unknown"
    ONLINE = "Online"
    ONLINE_COMMAND = "OnlineCommand"
    SLEEP = "Sleep"
    OFFLINE = "Offline"
    OFFLINE_TIMEOUT = "OfflineTimeout"
    OFFLINE_COMMAND = "OfflineCommand"
    CONNECTING = "Connecting"


CONNECTED_STATES = frozenset(
    {DeviceState.ONLINE, DeviceState.ONLINE_COMMAND, DeviceState.SLEEP}
)
VACATION_ON = "Aan"
VACATION_OFF = "Uit"


class ClimateConnectError(Exception):
    """Base error for the ClimateConnect client."""


class ClimateConnectAuthError(ClimateConnectError):
    """The credentials were rejected."""


class ClimateConnectUnexpectedPageError(ClimateConnectError):
    """The sign-in showed a page this client does not handle.

    For example multi-factor authentication, new terms of use or a changed layout.
    """

    def __init__(self, step: SignInStep) -> None:
        """Initialize the error with the sign-in step that failed."""
        super().__init__(f"Unexpected sign-in page at step '{step}'")
        self.step = step


class ClimateConnectConnectionError(ClimateConnectError):
    """A network error, timeout or server error; worth retrying later."""


class ClimateConnectRequestError(ClimateConnectError):
    """The service refused a request, for example an unsupported value."""


class ClimateConnectNotFoundError(ClimateConnectError):
    """The service does not know the boiler (HTTP 404)."""


class _TokenRejectedError(ClimateConnectError):
    """A service answered 401: the session has to be renewed."""


def create_cookie_jar() -> aiohttp.CookieJar:
    """Return a cookie jar that sends cookie values exactly as the server set them.

    Azure B2C's x-ms-cpim-* cookies are base64 and contain "=", "/" and "+". By
    default aiohttp quotes such values when sending them back, and B2C then rejects
    the sign-in because the CSRF cookie no longer matches the X-CSRF-TOKEN header.
    """
    return aiohttp.CookieJar(quote_cookie=False)


def jwt_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload without verifying it."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except IndexError, ValueError, binascii.Error:
        return {}
    return claims if isinstance(claims, dict) else {}


def _page_setting(page: str, key: str) -> str | None:
    """Pick a string field out of the B2C page's SETTINGS literal (JavaScript)."""
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', page)
    return match.group(1) if match else None


def _sign_in_settings(page: str) -> tuple[str, str, str, str]:
    """Return the CSRF token, transaction id, tenant path and policy of a B2C page."""
    csrf, trans_id, tenant, policy = (
        _page_setting(page, key) or ""
        for key in ("csrf", "transId", "tenant", "policy")
    )
    if (
        not (csrf and trans_id and policy)
        or not tenant.startswith("/")
        or _page_setting(page, "api") != EXPECTED_SIGN_IN_API
    ):
        raise ClimateConnectUnexpectedPageError(SignInStep.AUTHORIZE)
    return csrf, trans_id, tenant, policy


def _form_fields(page: str) -> tuple[str | None, dict[str, str]]:
    """Return the action and hidden fields of the auto-submitting form on a page."""
    action = re.search(r"<form[^>]*\baction=['\"]([^'\"]+)['\"]", page)
    fields = {
        name: unescape(value)
        for name, value in re.findall(
            r"<input[^>]*\bname=['\"]([^'\"]+)['\"][^>]*\bvalue=['\"]([^'\"]*)['\"]",
            page,
        )
    }
    return (unescape(action.group(1)) if action else None), fields


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_enum[E: StrEnum](enum: type[E], value: Any) -> E | None:
    """Parse an enum sent by name."""
    if isinstance(value, str):
        for member in enum:
            if member.value.lower() == value.lower():
                return member
    if value is not None:
        _LOGGER.debug("Unrecognised %s value: %r", enum.__name__, value)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _from_millis(value: Any) -> datetime | None:
    millis = _int(value)
    return datetime.fromtimestamp(millis / 1000, UTC) if millis is not None else None


def format_vacation_datetime(moment: datetime) -> str:
    """Format a moment the way the app sends it: 2026-12-23T18:00:00.000+01:00."""
    moment = moment.replace(microsecond=0)
    text = moment.isoformat(timespec="milliseconds")
    return text.replace("Z", "+00:00")


def clean_serial(serial: str) -> str:
    """Return the serial number as the wifi API expects it (no leading '#')."""
    return serial.strip().lstrip("#").strip().upper()


@dataclass(frozen=True, slots=True)
class Session:
    """A signed-in session."""

    token: str
    state: str
    refresh_token: str
    account_id: str
    renewed_at: datetime


@dataclass(frozen=True, slots=True)
class BoilerInfo:
    """A boiler of the account, as the app's back end lists it."""

    serial_number: str
    model: str | None
    item_code: str | None
    installation_date: date | None


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    """The boiler's status (GetDeviceStatus)."""

    device_state: DeviceState | None
    device_mode: DeviceMode | None
    boost_active: bool | None
    boiler_content: float | None
    power: float | None
    energy_consumption: float | None
    energy_saving: float | None
    error_number: int | None
    legionella_prevention_timer: int | None
    software_version: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, result: dict[str, Any]) -> DeviceStatus:
        """Build the status from the `result` object of the response."""
        boost = result.get("boostActive")
        return cls(
            device_state=_parse_enum(DeviceState, result.get("deviceState")),
            device_mode=_parse_enum(DeviceMode, result.get("deviceMode")),
            boost_active=boost if isinstance(boost, bool) else None,
            boiler_content=_float(result.get("boilerContent")),
            power=_float(result.get("devicePowerMeasured")),
            energy_consumption=_float(result.get("energyConsumption")),
            energy_saving=_float(result.get("energySaving")),
            error_number=_int(result.get("errorNumber")),
            legionella_prevention_timer=_int(result.get("legionellaPreventionTimer")),
            software_version=_str(result.get("deviceSoftwareVersion")),
            raw=result,
        )


type Schedule = dict[int, dict[int, float]]
"""Weekly schedule: weekday (0 = Monday) -> seconds after midnight -> temperature."""


def parse_schedule(value: Any) -> Schedule:
    """Parse the API's schedule: {"0": {"68400": 75}, ...}."""
    schedule: Schedule = {day: {} for day in range(7)}
    if not isinstance(value, dict):
        return schedule
    for day_key, switches in value.items():
        try:
            day = int(day_key)
        except TypeError, ValueError:
            continue
        if day not in schedule or not isinstance(switches, dict):
            continue
        for seconds_key, temperature in switches.items():
            try:
                seconds = int(seconds_key)
            except TypeError, ValueError:
                continue
            if (value_f := _float(temperature)) is not None:
                schedule[day][seconds] = value_f
    return schedule


def schedule_to_api(schedule: Schedule) -> dict[str, dict[str, float | int]]:
    """Format a schedule for UpdateDeviceMode."""
    return {
        str(day): {
            str(seconds): int(temp) if float(temp).is_integer() else temp
            for seconds, temp in sorted(schedule.get(day, {}).items())
        }
        for day in range(7)
    }


@dataclass(frozen=True, slots=True)
class DeviceModeSettings:
    """The boiler's mode, setpoint and schedule (GetDeviceMode)."""

    mode: DeviceMode | None
    temperature: float | None
    schedule: Schedule

    @classmethod
    def from_api(cls, result: dict[str, Any]) -> DeviceModeSettings:
        """Build the settings from the `result` object of the response."""
        return cls(
            mode=_parse_enum(DeviceMode, result.get("deviceMode")),
            temperature=_float(result.get("temperature")),
            schedule=parse_schedule(result.get("schedule")),
        )


@dataclass(frozen=True, slots=True)
class Fault:
    """A fault the boiler reported."""

    error_number: int
    started: datetime | None
    duration: int | None


@dataclass(frozen=True, slots=True)
class FaultText:
    """The app's texts for a fault code."""

    message: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class VacationMode:
    """Vacation mode: on or off, with the period the app keeps."""

    active: bool
    start: datetime | None
    end: datetime | None


@dataclass(frozen=True, slots=True)
class Tariff:
    """The electricity tariff the app uses for costs."""

    price: float | None
    start: date | None


@dataclass(frozen=True, slots=True)
class EnergyDay:
    """Energy use and costs of one day (the API's days start at 00:00 UTC)."""

    start: datetime
    consumption: float
    costs: float | None


class ClimateConnectClient:
    """Client for one ClimateConnect account.

    The session must have its own cookie jar: the sign-in and the Betty Blocks back
    end rely on cookies that must not leak into a shared jar.
    """

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        """Initialise the client. Credentials stay in memory only."""
        self._http = session
        self._email = email
        self._password = password
        self._session: Session | None = None
        self._lock = asyncio.Lock()

    @property
    def account_id(self) -> str | None:
        """The signed-in account's e-mail address, lower case."""
        return self._session.account_id if self._session else None

    @property
    def token_expires(self) -> datetime | None:
        """When the current JWT expires."""
        if self._session is None:
            return None
        session = jwt_claims(self._session.token).get("session")
        return (
            _parse_datetime(session.get("expires_at"))
            if isinstance(session, dict)
            else None
        )

    # Authentication

    async def async_sign_in(self) -> str:
        """Do a full sign-in. Returns the account id."""
        async with self._lock:
            self._session = await self._sign_in()
            return self._session.account_id

    async def _async_session(
        self, *, rejected: str | None = None, sign_in: bool = False
    ) -> Session:
        """Return a usable session: cached, renewed, or from a full sign-in.

        `rejected` is the token a service just refused: the session is renewed,
        unless a parallel call already replaced that token. `sign_in` skips renewal.
        """
        async with self._lock:
            session = self._session
            if session and rejected and session.token != rejected:
                return session
            fresh = (
                session and datetime.now(UTC) - session.renewed_at < TOKEN_RENEW_AFTER
            )
            if session and rejected is None and fresh:
                return session
            if session and not sign_in:
                try:
                    self._session = await self._renew(session)
                except (_TokenRejectedError, ClimateConnectRequestError) as err:
                    _LOGGER.debug("Session renewal refused (%s), signing in again", err)
                else:
                    return self._session
            self._session = await self._sign_in()
            return self._session

    async def _sign_in(self) -> Session:
        self._http.cookie_jar.clear()
        authorize_url = await self._initiate()
        page_url, page = await self._authorize(authorize_url)
        await self._submit_credentials(page_url, page)
        action, fields = await self._confirm(page_url, page)
        token = await self._callback(action, fields)
        return self._session_from_token(token)

    def _session_from_token(self, token: str) -> Session:
        claims = jwt_claims(token)
        session = claims.get("session")
        if not isinstance(session, dict):
            raise ClimateConnectUnexpectedPageError(SignInStep.CALLBACK)
        state = _str(session.get("state"))
        refresh = _str(session.get("refresh_token"))
        account = _account_id(claims)
        if not state or not refresh or not account:
            raise ClimateConnectUnexpectedPageError(SignInStep.CALLBACK)
        return Session(
            token=token,
            state=state,
            refresh_token=refresh,
            account_id=account,
            renewed_at=datetime.now(UTC),
        )

    async def _initiate(self) -> str:
        _LOGGER.debug("Sign-in: initiating")
        async with self._request(
            "GET",
            f"{BETTY_BASE}/sso/initiate",
            params={
                "application_id": APPLICATION_ID,
                "redirect": APP_REDIRECT,
                "ui_locales": "nl",
            },
        ) as resp:
            if resp.status != 200:
                msg = f"initiate returned HTTP {resp.status}"
                raise ClimateConnectConnectionError(msg)
            text = await resp.text()
        try:
            data = json.loads(text)
        except ValueError as err:
            # An HTML page here is a maintenance or error page: try again later.
            msg = "initiate returned no JSON"
            raise ClimateConnectConnectionError(msg) from err
        url = data.get("sso") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url.startswith(f"{B2C_HOST}/"):
            raise ClimateConnectUnexpectedPageError(SignInStep.INITIATE)
        return url

    async def _authorize(self, url: str) -> tuple[str, str]:
        _LOGGER.debug("Sign-in: fetching authorize page")
        async with self._request("GET", url) as resp:
            if resp.status != 200:
                msg = f"authorize returned HTTP {resp.status}"
                raise ClimateConnectConnectionError(msg)
            return str(resp.url), await resp.text()

    async def _submit_credentials(self, page_url: str, page: str) -> None:
        _LOGGER.debug("Sign-in: submitting credentials")
        csrf, trans_id, tenant, policy = _sign_in_settings(page)
        async with self._request(
            "POST",
            f"{B2C_HOST}{tenant}/SelfAsserted",
            params={"tx": trans_id, "p": policy},
            data={
                "request_type": "RESPONSE",
                "logonIdentifier": self._email,
                "password": self._password,
            },
            headers={
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Origin": B2C_HOST,
                "Referer": page_url,
            },
        ) as resp:
            if resp.status != 200:
                msg = f"SelfAsserted returned HTTP {resp.status}"
                raise ClimateConnectConnectionError(msg)
            try:
                reply = json.loads(await resp.text())
            except ValueError as err:
                raise ClimateConnectUnexpectedPageError(SignInStep.CREDENTIALS) from err
        status = str(reply.get("status")) if isinstance(reply, dict) else None
        if status == "400":
            # Wrong e-mail address or password. B2C's message may echo input, so it
            # is not logged.
            msg = "Credentials rejected"
            raise ClimateConnectAuthError(msg)
        if status != "200":
            raise ClimateConnectUnexpectedPageError(SignInStep.CREDENTIALS)

    async def _confirm(self, page_url: str, page: str) -> tuple[str, dict[str, str]]:
        _LOGGER.debug("Sign-in: confirming")
        csrf, trans_id, tenant, policy = _sign_in_settings(page)
        async with self._request(
            "GET",
            f"{B2C_HOST}{tenant}/api/{EXPECTED_SIGN_IN_API}/confirmed",
            params={
                "csrf_token": csrf,
                "tx": trans_id,
                "p": policy,
            },
            headers={"Referer": page_url},
        ) as resp:
            if resp.status != 200:
                msg = f"confirmed returned HTTP {resp.status}"
                raise ClimateConnectConnectionError(msg)
            text = await resp.text()
        action, fields = _form_fields(text)
        # B2C shows another page here for multi-factor authentication, new terms of
        # use or a password change.
        if (
            not action
            or urlsplit(action).netloc != SSO_CALLBACK_HOST
            or "code" not in fields
            or "state" not in fields
        ):
            raise ClimateConnectUnexpectedPageError(SignInStep.CONFIRM)
        return action, {"state": fields["state"], "code": fields["code"]}

    async def _callback(self, action: str, fields: dict[str, str]) -> str:
        _LOGGER.debug("Sign-in: redeeming the authorization code")
        async with self._request(
            "POST",
            action,
            data=fields,
            headers={"Origin": B2C_HOST, "Referer": f"{B2C_HOST}/"},
            allow_redirects=False,
        ) as resp:
            location = resp.headers.get("Location", "")
            if resp.status >= 500:
                msg = f"SSO callback returned HTTP {resp.status}"
                raise ClimateConnectConnectionError(msg)
            if resp.status not in (301, 302, 303) or not location.startswith(
                APP_REDIRECT
            ):
                raise ClimateConnectUnexpectedPageError(SignInStep.CALLBACK)
        token = parse_qs(urlsplit(location).query).get("token", [""])[0]
        if not token:
            raise ClimateConnectUnexpectedPageError(SignInStep.CALLBACK)
        return token

    async def _renew(self, session: Session) -> Session:
        _LOGGER.debug("Renewing the session")
        async with self._request(
            "GET",
            f"{BETTY_BASE}/sso/session/validate",
            params={"state": session.state, "refresh_token": session.refresh_token},
        ) as resp:
            if 400 <= resp.status < 500:
                msg = f"validate returned HTTP {resp.status}"
                raise _TokenRejectedError(msg)
            if resp.status != 200:
                msg = f"validate returned HTTP {resp.status}"
                raise ClimateConnectConnectionError(msg)
            text = await resp.text()
        try:
            token = json.loads(text)
        except ValueError:
            token = None
        if not isinstance(token, str) or token.count(".") != 2:
            msg = "validate returned no token"
            raise ClimateConnectRequestError(msg)
        try:
            renewed = self._session_from_token(token)
        except ClimateConnectUnexpectedPageError as err:
            msg = "validate returned an unusable token"
            raise ClimateConnectRequestError(msg) from err
        if renewed.account_id != session.account_id:
            msg = "validate returned a token for another account"
            raise ClimateConnectRequestError(msg)
        return renewed

    def _request(self, method: str, url: str, **kwargs: Any) -> _ErrorMapping:
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return _ErrorMapping(self._http.request(method, url, **kwargs))

    # Requests with the session

    async def _call(
        self,
        service: str,
        method: str,
        path: str,
        *,
        expect_json: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Call the wifi API or the back end.

        On HTTP 401 the session is renewed; if the service still refuses it, the
        client signs in again (which also gets a new back end session cookie).
        """
        rejected: str | None = None
        for attempt in range(3):
            session = await self._async_session(rejected=rejected, sign_in=attempt == 2)
            if service == "wifi":
                url = f"{WIFI_API}/{path}"
                headers = {"Authorization": f"Bearer {session.token}"}
            else:
                url = f"{BETTY_BASE}{path}"
                headers = {"application-id": APPLICATION_ID, "state": session.state}
            headers["Accept"] = "application/json"
            try:
                return await self._send(
                    method, url, path, headers, expect_json=expect_json, **kwargs
                )
            except _TokenRejectedError:
                _LOGGER.debug("%s: session rejected (attempt %d)", path, attempt + 1)
                rejected = session.token
        msg = f"{path}: session rejected after signing in again"
        raise ClimateConnectConnectionError(msg)

    async def _send(
        self,
        method: str,
        url: str,
        path: str,
        headers: dict[str, str],
        *,
        expect_json: bool,
        **kwargs: Any,
    ) -> Any:
        try:
            async with self._request(method, url, headers=headers, **kwargs) as resp:
                body = await resp.read()
                status = resp.status
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = f"{path}: {type(err).__name__}"
            raise ClimateConnectConnectionError(msg) from err
        if status in (401, 403):
            msg = f"{path}: HTTP {status}"
            raise _TokenRejectedError(msg)
        if status == 404:
            raise ClimateConnectNotFoundError(path)
        if status == 400:
            msg = f"{path}: {_error_message(body, status)}"
            raise ClimateConnectRequestError(msg)
        if status >= 400:
            msg = f"{path}: HTTP {status}"
            raise ClimateConnectConnectionError(msg)
        if not expect_json:
            return body.decode("utf-8", "replace")
        if not body.strip():
            return None
        try:
            return json.loads(body)
        except ValueError:
            # The back end answers some refusals with HTTP 200 and plain text.
            text = body.decode("utf-8", "replace").strip()
            msg = f"{path}: {text[:200]}"
            raise ClimateConnectRequestError(msg) from None

    async def _wifi_result(self, path: str, serial: str) -> dict[str, Any]:
        data = await self._call("wifi", "GET", path, params={"serialNumber": serial})
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            msg = f"{path}: response without result"
            raise ClimateConnectConnectionError(msg)
        return result

    async def _betty_json(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        data = await self._call("betty", method, path, **kwargs)
        if not isinstance(data, dict):
            msg = f"{path}: unexpected response"
            raise ClimateConnectConnectionError(msg)
        return data

    # Boilers

    async def async_get_boilers(self) -> list[BoilerInfo]:
        """Return the boilers registered to the account."""
        data = await self._betty_json("GET", "/api/consumer-app/device")
        boilers: list[BoilerInfo] = []
        for device in data.get("devices") or []:
            if not isinstance(device, dict) or not _str(device.get("serial_number")):
                continue
            registration = device.get("product_registration")
            if not isinstance(registration, dict):
                registration = {}
            boilers.append(
                BoilerInfo(
                    serial_number=clean_serial(device["serial_number"]),
                    model=_str(registration.get("item_name")),
                    item_code=_str(registration.get("item_code")),
                    installation_date=_parse_date(
                        registration.get("installation_date")
                    ),
                )
            )
        return boilers

    async def async_get_device_status(self, serial: str) -> DeviceStatus:
        """Return the boiler's status."""
        return DeviceStatus.from_api(await self._wifi_result("GetDeviceStatus", serial))

    async def async_get_device_mode(self, serial: str) -> DeviceModeSettings:
        """Return the boiler's mode, setpoint and schedule."""
        return DeviceModeSettings.from_api(
            await self._wifi_result("GetDeviceMode", serial)
        )

    async def async_set_device_mode(
        self,
        serial: str,
        mode: DeviceMode,
        *,
        temperature: float | None = None,
        schedule: Schedule | None = None,
    ) -> None:
        """Change the mode; Continuous takes a temperature, Schedule a schedule."""
        payload: dict[str, Any] = {"serialNumber": serial, "deviceMode": mode.value}
        if temperature is not None:
            payload["temperature"] = (
                int(temperature) if float(temperature).is_integer() else temperature
            )
        if schedule is not None:
            payload["schedule"] = schedule_to_api(schedule)
        await self._call("wifi", "POST", "UpdateDeviceMode", json=payload)

    async def async_set_boost(self, serial: str, *, active: bool) -> None:
        """Turn the boost on or off."""
        await self._call(
            "wifi",
            "POST",
            "BoostBoiler",
            json={"activateBoost": active, "serialNumber": serial},
        )

    async def async_get_fault_history(self, serial: str) -> list[Fault]:
        """Return the latest faults (at most 8), newest first."""
        data = await self._call(
            "wifi", "GET", "GetFaultHistory", params={"serialNumber": serial}
        )
        faults = [
            Fault(
                error_number=number,
                started=_from_millis(item.get("timeStamp")),
                duration=_int(item.get("duration")),
            )
            for item in (data.get("results") or [] if isinstance(data, dict) else [])
            if isinstance(item, dict)
            and (number := _int(item.get("errorNumber"))) is not None
        ]
        faults.sort(
            key=lambda fault: fault.started or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return faults

    async def async_get_fault_texts(self, serial: str) -> dict[int, FaultText]:
        """Return the app's (Dutch) texts per fault code."""
        data = await self._betty_json(
            "GET", f"/api/consumer-app/device/{serial}/malfunction-code-translation"
        )
        return {
            code: FaultText(
                message=_str(item.get("fault_message")),
                description=_str(item.get("fault_description")),
            )
            for item in data.get("device_fault_details") or []
            if isinstance(item, dict)
            and (code := _int(item.get("fault_code"))) is not None
        }

    async def async_get_energy_days(
        self, serial: str, now: datetime
    ) -> list[EnergyDay]:
        """Return the energy use per day for the whole history window."""
        result = await self._call(
            "wifi",
            "GET",
            "GetEnergyConsumption",
            params={
                "serialNumber": serial,
                "startDate": str(int((now - HISTORY_WINDOW).timestamp() * 1000)),
                "endDate": str(int(now.timestamp() * 1000)),
                "interval": "Day",
                "includePreviousPeriod": "false",
            },
        )
        result = result.get("result") if isinstance(result, dict) else None
        rows = result.get("data") if isinstance(result, dict) else None
        days: list[EnergyDay] = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            start = _from_millis(item.get("timeStamp"))
            consumption = _float(item.get("consumption"))
            if start is None or consumption is None:
                continue
            days.append(
                EnergyDay(
                    start=start,
                    consumption=consumption,
                    costs=_float(item.get("costs")),
                )
            )
        days.sort(key=lambda day: day.start)
        return days

    # Vacation mode and tariff (app back end)

    async def async_get_vacation_mode(self, serial: str) -> VacationMode:
        """Return vacation mode and its period."""
        data = await self._betty_json(
            "GET", f"/api/consumer-app/device/{serial}/get-vacation-mode"
        )
        return VacationMode(
            active=data.get("status") == VACATION_ON,
            start=_parse_datetime(data.get("start_date")),
            end=_parse_datetime(data.get("end_date")),
        )

    async def async_set_vacation_mode(
        self, serial: str, *, active: bool, start: datetime, end: datetime
    ) -> None:
        """Turn vacation mode on or off, with its period."""
        data = await self._betty_json(
            "POST",
            f"/api/consumer-app/device/{serial}/vacation-mode",
            json={
                "startdate": format_vacation_datetime(start),
                "mode": VACATION_ON if active else VACATION_OFF,
                "enddate": format_vacation_datetime(end),
            },
        )
        expected = VACATION_ON if active else VACATION_OFF
        if data.get("mode") not in (None, expected):
            msg = f"vacation-mode: {data.get('message') or data.get('mode')}"
            raise ClimateConnectRequestError(msg)

    async def async_get_tariff(self, serial: str, today: date) -> Tariff:
        """Return the tariff that applies today."""
        data = await self._betty_json(
            "GET", f"/api/consumer-app/device/{serial}/tariff"
        )
        current: Tariff | None = None
        for item in data.get("tariffs") or []:
            if not isinstance(item, dict):
                continue
            start = _parse_date(item.get("start_date"))
            end = _parse_date(item.get("end_date"))
            if (start and start > today) or (end and end < today):
                continue
            if current is None or (
                start and (current.start is None or start > current.start)
            ):
                current = Tariff(price=_float(item.get("tariff")), start=start)
        return current or Tariff(price=None, start=None)

    async def async_set_tariff(self, price: float, start: date) -> None:
        """Set the electricity tariff (per kWh) from `start` on, for the account.

        The back end answers with HTTP 200 and the account's tariffs as a JSON
        fragment (`"tariffs": [...]`), or with a plain-text refusal.
        """
        reply = await self._call(
            "betty",
            "POST",
            "/api/consumer-app/tariff",
            expect_json=False,
            json={
                "tariff": {"tariff": price, "start_date": start.strftime("%d-%m-%Y")}
            },
        )
        if '"tariffs"' not in reply:
            msg = f"tariff: {reply.strip()[:200] or 'empty response'}"
            raise ClimateConnectRequestError(msg)


def _account_id(claims: dict[str, Any]) -> str | None:
    for key in ("consumer", "user"):
        part = claims.get(key)
        if isinstance(part, dict) and (mail := _str(part.get("mailaddress"))):
            return mail.strip().lower()
    return None


def _error_message(body: bytes, status: int) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return f"HTTP {status}"
    message = data.get("message") if isinstance(data, dict) else None
    if isinstance(message, dict):
        message = message.get("message")
    return message if isinstance(message, str) and message else f"HTTP {status}"


class _ErrorMapping:
    """Async context manager that turns aiohttp errors into connection errors."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def __aenter__(self) -> aiohttp.ClientResponse:
        try:
            response: aiohttp.ClientResponse = await self._ctx.__aenter__()
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = type(err).__name__
            raise ClimateConnectConnectionError(msg) from err
        return response

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._ctx.__aexit__(exc_type, exc, tb)
        if isinstance(exc, TimeoutError | aiohttp.ClientError):
            msg = type(exc).__name__
            raise ClimateConnectConnectionError(msg) from exc
