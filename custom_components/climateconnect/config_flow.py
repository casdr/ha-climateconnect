"""Config flow for ClimateConnect."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    ClimateConnectAuthError,
    ClimateConnectClient,
    ClimateConnectConnectionError,
    ClimateConnectError,
    ClimateConnectUnexpectedPageError,
    create_cookie_jar,
)
from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    DOMAIN,
    MAX_UPDATE_INTERVAL_SECONDS,
    MIN_UPDATE_INTERVAL_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .coordinator import ClimateConnectConfigEntry

_LOGGER = logging.getLogger(__name__)

EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


class NoBoilersError(Exception):
    """The account has no boilers."""


async def validate_credentials(hass: HomeAssistant, email: str, password: str) -> str:
    """Sign in once, check for boilers and return the account id."""
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=create_cookie_jar()
    )
    try:
        client = ClimateConnectClient(session, email, password)
        account_id = await client.async_sign_in()
        if not await client.async_get_boilers():
            raise NoBoilersError
        return account_id
    finally:
        session.detach()


async def _errors_for(
    hass: HomeAssistant, email: str, password: str
) -> tuple[str | None, dict[str, str]]:
    """Validate credentials and map failures to form errors.

    Error messages hold only a step name, path, HTTP status or exception type, so
    they are logged at debug level to explain the form error.
    """
    try:
        return await validate_credentials(hass, email, password), {}
    except ClimateConnectAuthError as err:
        _LOGGER.debug("Sign-in check: credentials rejected (%s)", err)
        return None, {"base": "invalid_auth"}
    except ClimateConnectUnexpectedPageError as err:
        _LOGGER.debug("Sign-in check: %s", err)
        return None, {"base": "unexpected_page"}
    except (ClimateConnectConnectionError, ClimateConnectError) as err:
        _LOGGER.debug("Sign-in check: cannot connect (%s)", err)
        return None, {"base": "cannot_connect"}
    except NoBoilersError:
        return None, {"base": "no_boilers"}
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error while validating ClimateConnect sign-in")
        return None, {"base": "unknown"}


def _credentials_schema() -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_EMAIL): EMAIL_SELECTOR,
            probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        }
    )


class ClimateConnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ClimateConnect."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for e-mail and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            account_id, errors = await _errors_for(
                self.hass, email, user_input[CONF_PASSWORD]
            )
            if account_id:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _credentials_schema(),
                {CONF_EMAIL: user_input[CONF_EMAIL]} if user_input else {},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        _entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Start reauthentication after the stored password was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account_id, errors = await _errors_for(
                self.hass, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if account_id:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR}
            ),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the password.

        The account is identified by its e-mail address, so another address is
        another account: that is added as a new entry.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account_id, errors = await _errors_for(
                self.hass, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if account_id:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR}
            ),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ClimateConnectConfigEntry,  # noqa: ARG004 - Home Assistant's signature
    ) -> ClimateConnectOptionsFlow:
        """Return the options flow."""
        return ClimateConnectOptionsFlow()


class ClimateConnectOptionsFlow(OptionsFlowWithReload):
    """Options: update interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the update interval in seconds."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL])}
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_UPDATE_INTERVAL): NumberSelector(
                            NumberSelectorConfig(
                                min=MIN_UPDATE_INTERVAL_SECONDS,
                                max=MAX_UPDATE_INTERVAL_SECONDS,
                                step=1,
                                mode=NumberSelectorMode.BOX,
                                unit_of_measurement="s",
                            )
                        )
                    }
                ),
                {
                    CONF_UPDATE_INTERVAL: self.config_entry.options.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_SECONDS
                    )
                },
            ),
        )
