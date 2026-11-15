"""Config flow tests: sign-in, errors, reauth, reconfigure and options."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.climateconnect.api import (
    ClimateConnectAuthError,
    ClimateConnectConnectionError,
    ClimateConnectUnexpectedPageError,
    SignInStep,
)
from custom_components.climateconnect.const import CONF_UPDATE_INTERVAL, DOMAIN

from .helpers import EMAIL, PASSWORD

if TYPE_CHECKING:
    from collections.abc import Generator

    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .fake_client import FakeClient

pytestmark = pytest.mark.usefixtures("mock_client", "mock_setup_entry")


@pytest.fixture(name="mock_setup_entry")
def mock_setup_entry_fixture() -> Generator[AsyncMock]:
    """Skip setting up the entry after the flow."""
    with patch(
        "custom_components.climateconnect.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def test_user_flow(hass: HomeAssistant, client: FakeClient) -> None:
    """Signing in creates an entry for the account."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": f" {EMAIL} ", "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {"email": EMAIL, "password": PASSWORD}
    assert result["result"].unique_id == EMAIL
    assert client.sign_ins == 1


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (ClimateConnectAuthError("rejected"), "invalid_auth"),
        (ClimateConnectConnectionError("timeout"), "cannot_connect"),
        (ClimateConnectUnexpectedPageError(SignInStep.CONFIRM), "unexpected_page"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors_then_recover(
    hass: HomeAssistant, client: FakeClient, error: Exception, key: str
) -> None:
    """A failed sign-in shows an error; a retry succeeds."""
    client.fail_with = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    client.fail_with = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_without_boilers(
    hass: HomeAssistant, client: FakeClient
) -> None:
    """An account without boilers cannot be added."""
    client.boilers = []
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["errors"] == {"base": "no_boilers"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The same account cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": EMAIL, "password": PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Reauthentication stores the new password."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    client.fail_with = ClimateConnectAuthError("rejected")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "still wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    client.fail_with = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["password"] == "new password"


async def test_reauth_wrong_account(
    hass: HomeAssistant, config_entry: MockConfigEntry, client: FakeClient
) -> None:
    """Credentials of another account are refused."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    with patch.object(
        client, "async_sign_in", AsyncMock(return_value="other@example.com")
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "new password"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_reconfigure(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Reconfiguring changes the password of the same account."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "changed"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data["password"] == "changed"


async def test_options(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """The update interval can be changed."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_UPDATE_INTERVAL: 120}
